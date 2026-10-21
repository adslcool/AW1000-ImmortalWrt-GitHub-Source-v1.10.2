"""Generate the optional, project-owned first-boot onboarding package.

A shared default key is embedded for private builds; BOARD_STATE_KEY overrides it.
Names are neutral labels, not a claim of tamper-proof key storage.
"""
from pathlib import Path
import hashlib
import os
import re
import secrets
import shutil
import struct

# Private distribution only: source and firmware expose this recoverable shared key.
DEFAULT_KEY_HEX = 'ee559d578652553f5cb396befb736c3f111e659a82985f8ed50f82ded222c515'

PACKAGE = 'aw1000-board-state'
BINARY = 'usr/libexec/aw1000-board/stage1'
STATE = '/etc/aw1000-board-state'
RUNTIME_FILES = [BINARY, 'usr/libexec/aw1000-board/runner', 'etc/init.d/aw1000-board-state']


def enabled(settings):
    value = settings.get('include_device_init', False)
    if type(value) is not bool:
        raise ValueError('include_device_init must be a boolean')
    return value


def key_header(text):
    if not re.fullmatch(r'[0-9a-fA-F]{64}', text):
        raise ValueError('BOARD_STATE_KEY must contain 64 hexadecimal characters')
    key = bytes.fromhex(text)
    mask = secrets.token_bytes(32)
    other = bytes(a ^ b for a, b in zip(key, mask))
    def array(name, value):
        return 'static volatile const unsigned char '+name+'[32] = {'+','.join(str(x) for x in value)+'};\n'
    return (array('key_a', mask) + array('key_b', other)
            + 'static const char key_id[]="'+hashlib.sha256(key).hexdigest()[:16]+'";\n')


def config(text, selected):
    lines = [line for line in text.splitlines() if not line.removeprefix('# ').startswith('CONFIG_PACKAGE_'+PACKAGE)]
    lines.append('CONFIG_PACKAGE_'+PACKAGE+'=y' if selected else '# CONFIG_PACKAGE_'+PACKAGE+' is not set')
    return '\n'.join(lines)+'\n'


MAKEFILE = r'''include $(TOPDIR)/rules.mk
PKG_NAME:=aw1000-board-state
PKG_VERSION:=1.10.2
PKG_RELEASE:=1
PKG_LICENSE:=MIT AND Apache-2.0
PKG_BUILD_DEPENDS:=openssl
include $(INCLUDE_DIR)/package.mk

define Package/aw1000-board-state
  SECTION:=utils
  CATEGORY:=Utilities
  TITLE:=AW1000 first-boot onboarding
  DEPENDS:=+tom_modem +uci +coreutils-timeout
endef

define Build/Prepare
	$(call Build/Prepare/Default)
	$(CP) ./src/stage1.c ./src/board_key.h $(PKG_BUILD_DIR)/
endef

define Build/Compile
	$(TARGET_CC) $(TARGET_CPPFLAGS) $(TARGET_CFLAGS) -std=c99 -Os -ffunction-sections -fdata-sections \
		-static -I$(STAGING_DIR)/usr/include -o $(PKG_BUILD_DIR)/stage1 $(PKG_BUILD_DIR)/stage1.c \
		$(STAGING_DIR)/usr/lib/libcrypto.a -lcrypt -lpthread -ldl \
		$(if $(CONFIG_OPENSSL_WITH_COMPRESSION),-lz) $(TARGET_LDFLAGS) -Wl,--gc-sections
endef

define Package/aw1000-board-state/install
	$(INSTALL_DIR) $(1)/usr/libexec/aw1000-board $(1)/etc/init.d
	$(INSTALL_BIN) $(PKG_BUILD_DIR)/stage1 $(1)/usr/libexec/aw1000-board/
	chmod 700 $(1)/usr/libexec/aw1000-board/stage1
	$(INSTALL_BIN) ./files/runner $(1)/usr/libexec/aw1000-board/
	$(INSTALL_BIN) ./files/service $(1)/etc/init.d/aw1000-board-state
endef

$(eval $(call BuildPackage,aw1000-board-state))
'''

SERVICE = r'''#!/bin/sh /etc/rc.common
# shellcheck shell=ash disable=SC2034
START=99
STOP=10
USE_PROCD=1
start_service() {
    procd_open_instance
    procd_set_param command /usr/libexec/aw1000-board/runner
    procd_set_param stdout 1
    procd_set_param stderr 1
    procd_close_instance
}
'''

RUNNER = r'''#!/bin/sh
set -eu
STATE=/etc/aw1000-board-state
cleanup() {
    /etc/init.d/aw1000-board-state disable
    rm -f "$STATE/requested" "$STATE/lock"
    rm -f /usr/libexec/aw1000-board/stage1
    rm -f /etc/init.d/aw1000-board-state /usr/libexec/aw1000-board/runner
    rmdir /usr/libexec/aw1000-board 2>/dev/null || :
}
count=0
while [ "$count" -lt 360 ]; do
    if [ -f /etc/aw1000-provisioned ]; then
        if [ ! -f "$STATE/requested" ] && [ ! -f "$STATE/ready" ] && [ ! -f "$STATE/pending" ]; then
            logger -t aw1000-board-state 'Existing settings preserved; onboarding skipped'
            cleanup
            exit 0
        fi
        if /usr/libexec/aw1000-board/stage1 --init; then
            [ -f "$STATE/ready" ] || exit 1
            touch /etc/sysupgrade.conf
            grep -qxF "$STATE/ready" /etc/sysupgrade.conf || printf '%s\n' "$STATE/ready" >> /etc/sysupgrade.conf
            sync
            cleanup
            logger -t aw1000-board-state 'Onboarding verified; startup entry disabled'
            exit 0
        else
            code=$?
            case "$code" in
                2|3) logger -t aw1000-board-state 'Onboarding requires operator review; component retained'; exit "$code";;
            esac
        fi
    fi
    count=$((count + 1))
    sleep 5
done
logger -t aw1000-board-state 'Onboarding timed out; component retained for next boot'
exit 1
'''


def install(tree, settings):
    tree = Path(tree)
    package = tree/'package/custom'/PACKAGE
    link = tree/'files/etc/rc.d/S99aw1000-board-state'
    if not enabled(settings):
        if package.exists():
            shutil.rmtree(package)
        if link.is_symlink():
            link.unlink()
        return
    (package/'src').mkdir(parents=True, exist_ok=True)
    (package/'files').mkdir(exist_ok=True)
    (package/'src/stage1.c').write_text(C_SOURCE)
    header = package/'src/board_key.h'
    header.write_text(key_header(settings.get('board_state_key', '')))
    header.chmod(0o600)
    (package/'Makefile').write_text(MAKEFILE)
    (package/'files/runner').write_text(RUNNER)
    (package/'files/service').write_text(SERVICE)
    link.parent.mkdir(parents=True, exist_ok=True)
    if link.is_symlink():
        link.unlink()
    link.symlink_to('../init.d/aw1000-board-state')


def validate_root(root, selected):
    root = Path(root)
    binary = root/BINARY
    if not selected:
        if any((root/name).exists() for name in RUNTIME_FILES):
            raise RuntimeError('Unselected onboarding files remain in the image')
        return
    if any(not (root/name).is_file() for name in RUNTIME_FILES):
        raise RuntimeError('Selected onboarding runtime is missing')
    data = binary.read_bytes()
    if len(data) < 64 or data[:6] != b'\x7fELF\x02\x01' or struct.unpack_from('<H', data, 18)[0] != 183:
        raise RuntimeError('Onboarding executable is not a little-endian AArch64 ELF64 file')
    offset = struct.unpack_from('<Q', data, 32)[0]
    size, number = struct.unpack_from('<HH', data, 54)
    if size < 56 or offset + size*number > len(data):
        raise RuntimeError('Invalid onboarding program headers')
    for index in range(number):
        start = offset + size*index
        kind = struct.unpack_from('<I', data, start)[0]
        if kind == 3:
            raise RuntimeError('Onboarding executable unexpectedly requires an ELF interpreter')
        if kind == 2:
            dynamic_offset = struct.unpack_from('<Q', data, start+8)[0]
            dynamic_size = struct.unpack_from('<Q', data, start+32)[0]
            if dynamic_offset+dynamic_size > len(data):
                raise RuntimeError('Invalid onboarding dynamic section')
            for pos in range(dynamic_offset, dynamic_offset+dynamic_size, 16):
                if pos+16 > len(data):
                    raise RuntimeError('Truncated onboarding dynamic entry')
                if struct.unpack_from('<Q', data, pos)[0] == 1:
                    raise RuntimeError('Onboarding executable has an unexpected shared-library dependency')


C_SOURCE = r'''#define _POSIX_C_SOURCE 200809L
#include <ctype.h>
#include <crypt.h>
#include <errno.h>
#include <fcntl.h>
#include <openssl/crypto.h>
#include <openssl/evp.h>
#include <openssl/hmac.h>
#include <signal.h>
#include <shadow.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <sys/file.h>
#include <sys/resource.h>
#include <sys/stat.h>
#include <sys/types.h>
#include <sys/wait.h>
#include <unistd.h>
#include "board_key.h"

#ifndef STATE_DIR
#define STATE_DIR "/etc/aw1000-board-state"
#endif
#define DONE STATE_DIR "/ready"
#define PENDING STATE_DIR "/pending"

static int verify_password(const char *password) {
    const char *stored = NULL;
#ifdef AW_TEST
    /* Test builds use a private fake shadow file; never the host root account. */
    char buffer[512];
    FILE *file = fopen(STATE_DIR "/test-shadow", "r");
    if (!file) return -1;
    if (fgets(buffer, sizeof buffer, file)) {
        buffer[strcspn(buffer, "\r\n")] = 0;
        stored = buffer;
    }
    fclose(file);
#else
    struct spwd *entry = getspnam("root");
    if (entry) stored = entry->sp_pwdp;
#endif
    if (!stored || !*stored || stored[0] == '!' || stored[0] == '*') return -1;
    char *computed = crypt(password, stored);
    if (!computed || strlen(computed) != strlen(stored)) return -1;
    return CRYPTO_memcmp(computed, stored, strlen(stored)) == 0 ? 0 : -1;
}

static int derive(const char *imei, char out[9]) {
    unsigned char key[32], digest[EVP_MAX_MD_SIZE];
    const char alphabet[] = "abcdefghijklmnopqrstuvwxyz0123456789";
    unsigned int n = 0;
    char message[100];
    if (strlen(imei) != 15 || strspn(imei, "0123456789") != 15) return -1;
    for (int i = 0; i < 32; i++) key[i] = key_a[i] ^ key_b[i];
    for (int counter = 0; counter < 100; counter++) {
        snprintf(message, sizeof message, "aw1000:root-password:v2:%s:%d", imei, counter);
        if (!HMAC(EVP_sha256(), key, sizeof key, (unsigned char *)message,
                  strlen(message), digest, &n)) break;
        int pos = 0, letter = 0, number = 0;
        for (unsigned int i = 0; i < n && pos < 8; i++) {
            if (digest[i] >= 252) continue;
            char c = alphabet[digest[i] % 36];
            out[pos++] = c;
            if (c >= 'a' && c <= 'z') letter = 1; else number = 1;
        }
        out[pos] = 0;
        if (pos == 8 && letter && number) {
            OPENSSL_cleanse(key, sizeof key);
            OPENSSL_cleanse(digest, sizeof digest);
            return 0;
        }
    }
    OPENSSL_cleanse(key, sizeof key);
    OPENSSL_cleanse(digest, sizeof digest);
    return -1;
}

/* Execute without a shell. IMEI, passwords and keys never enter shell commands. */
static int run(char *const argv[], const char *input, char *out, size_t cap) {
    int pipefd[2], status;
    if (pipe(pipefd)) return -1;
    pid_t pid = fork();
    if (pid < 0) { close(pipefd[0]); close(pipefd[1]); return -1; }
    if (pid == 0) {
        int nullfd = open("/dev/null", O_RDWR);
        if (nullfd < 0) _exit(126);
        if (input) {
            dup2(pipefd[0], STDIN_FILENO);
            dup2(nullfd, STDOUT_FILENO);
        } else {
            dup2(nullfd, STDIN_FILENO);
            dup2(pipefd[1], STDOUT_FILENO);
        }
        dup2(nullfd, STDERR_FILENO);
        close(nullfd); close(pipefd[0]); close(pipefd[1]);
        execvp(argv[0], argv);
        _exit(127);
    }
    if (input) {
        close(pipefd[0]);
        size_t len = strlen(input), pos = 0;
        while (pos < len) {
            ssize_t got = write(pipefd[1], input + pos, len - pos);
            if (got < 0 && errno == EINTR) continue;
            if (got <= 0) break;
            pos += (size_t)got;
        }
        close(pipefd[1]);
    } else {
        char discard[256];
        size_t pos = 0;
        int overflow = 0;
        close(pipefd[1]);
        for (;;) {
            ssize_t got = read(pipefd[0], discard, sizeof discard);
            if (got < 0 && errno == EINTR) continue;
            if (got <= 0) break;
            if (pos + (size_t)got >= cap) overflow = 1;
            if (!overflow) { memcpy(out + pos, discard, (size_t)got); pos += (size_t)got; }
        }
        close(pipefd[0]);
        out[pos] = 0;
        if (overflow) out[0] = 0;
    }
    while (waitpid(pid, &status, 0) < 0) { if (errno != EINTR) return -1; }
    return WIFEXITED(status) && WEXITSTATUS(status) == 0 ? 0 : -1;
}

static char *trim(char *s) {
    while (isspace((unsigned char)*s)) s++;
    size_t n = strlen(s);
    while (n && isspace((unsigned char)s[n - 1])) s[--n] = 0;
    return s;
}

static int parse_imei(char *response, char imei[16]) {
    int ok = 0, bad = 0, count = 0;
    char *save = NULL;
    for (char *line = strtok_r(response, "\n", &save); line; line = strtok_r(NULL, "\n", &save)) {
        char *s = trim(line);
        if (!strcmp(s, "OK")) ok = 1;
        if (strstr(s, "ERROR")) bad = 1;
        if (!strncmp(s, "+CGSN:", 6)) s = trim(s + 6);
        size_t n = strlen(s);
        if (n >= 2 && s[0] == '"' && s[n - 1] == '"') { s[n - 1] = 0; s++; }
        if (strlen(s) == 15 && strspn(s, "0123456789") == 15) {
            memcpy(imei, s, 16); count++;
        }
    }
    return ok && !bad && count == 1 ? 0 : -1;
}

static int read_imei(char imei[16]) {
    char buffer[4096], port[128];
    char *config[] = {"timeout", "10", "uci", "-q", "get", "qmodem.4_1.at_port", NULL};
    if (run(config, NULL, buffer, sizeof buffer)) return -1;
    char *value = trim(buffer);
    if (strlen(value) >= sizeof port) return -1;
    const char *suffix = NULL;
    if (!strncmp(value, "/dev/ttyUSB", 11)) suffix = value + 11;
    if (!strncmp(value, "/dev/ttyACM", 11)) suffix = value + 11;
    if (!suffix || !*suffix || strspn(suffix, "0123456789") != strlen(suffix)) return -1;
    strcpy(port, value);
    char *queries[] = {"AT+CGSN", "AT+GSN"};
    for (int i = 0; i < 2; i++) {
        char *args[] = {"timeout", "10", "tom_modem", "-u", "-d", port, "-o", "a", "-c", queries[i], NULL};
        if (!run(args, NULL, buffer, sizeof buffer) && !parse_imei(buffer, imei)) return 0;
    }
    return -1;
}

static int trusted_record(void) {
    FILE *file = fopen(DONE, "r");
    if (!file) return 0;
    char line[200];
    int matching_key = 0, verified = 0;
    while (fgets(line, sizeof line, file)) {
        line[strcspn(line, "\r\n")] = 0;
        if (!strncmp(line, "key_id=", 7) && !strcmp(line + 7, key_id)) matching_key = 1;
        if (!strcmp(line, "verification=crypt-v1")) verified = 1;
    }
    int good = !ferror(file) && matching_key && verified;
    fclose(file);
    return good;
}

static int initialize(int repair) {
    if (mkdir(STATE_DIR, 0700) && errno != EEXIST) return 1;
    int dirfd = open(STATE_DIR, O_RDONLY | O_DIRECTORY | O_NOFOLLOW | O_CLOEXEC);
    if (dirfd < 0) return 1;
    int lock = open(STATE_DIR "/lock", O_CREAT | O_RDWR | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (lock < 0 || flock(lock, LOCK_EX | LOCK_NB)) {
        if (lock >= 0) close(lock);
        close(dirfd); return 1;
    }
    int result = 1;
    char imei[16] = {0}, password[9] = {0}, input[20] = {0};
    if (!access(PENDING, F_OK)) {
        fputs("存在未完成记录，为避免覆盖密码已停止；请人工检查\n", stderr);
        result = 2; goto end;
    }
    if (!repair && !access(DONE, F_OK)) {
        if (trusted_record()) {
            puts("已有本密钥的哈希核验标记，未更改密码；仅清理组件");
            result = 0;
        } else {
            fputs("旧版标记不能证明本密钥曾改密成功；未改密、未清理。如确认修复，请显式使用--repair\n", stderr);
            result = 3;
        }
        goto end;
    }
    if (read_imei(imei) || derive(imei, password)) {
        fputs("IMEI读取或生成失败，未更改密码\n", stderr); goto end;
    }
    int pending = open(PENDING, O_WRONLY | O_CREAT | O_EXCL | O_NOFOLLOW | O_CLOEXEC, 0600);
    if (pending < 0) goto end;
    char record[160];
    int len = snprintf(record, sizeof record, "algorithm=v2\nimei=%s\nkey_id=%s\nverification=crypt-v1\n", imei, key_id);
    int written = write(pending, record, (size_t)len) == len;
    int synced = fsync(pending) == 0;
    close(pending);
    if (!written || !synced || fsync(dirfd)) { result = 2; goto end; }
    int matched = repair && verify_password(password) == 0;
    if (!matched) {
        snprintf(input, sizeof input, "%s\n%s\n", password, password);
        char *args[] = {"timeout", "20", "passwd", "root", NULL};
        if (run(args, input, NULL, 0)) {
            fputs("passwd未确认成功，保留pending记录，停止自动重试\n", stderr);
            result = 2; goto end;
        }
    }
    if (verify_password(password)) {
        fputs("root密码哈希核对失败；保留pending记录和程序，请人工检查\n", stderr);
        result = 2; goto end;
    }
    if (rename(PENDING, DONE) || fsync(dirfd)) {
        fputs("密码可能已设置；初始化记录保存异常，请人工检查\n", stderr);
        result = 2; goto end;
    }
    puts(matched ? "当前root密码已与派生密码一致；已核对哈希并更新标记，未再次改密" : "root密码已设置且哈希核对成功，后续普通运行不会覆盖");
    result = 0;
end:
    OPENSSL_cleanse(password, sizeof password);
    OPENSSL_cleanse(input, sizeof input);
    close(lock); close(dirfd);
    return result;
}

int main(int argc, char **argv) {
    struct rlimit no_core = {0, 0};
    setrlimit(RLIMIT_CORE, &no_core);
    signal(SIGPIPE, SIG_IGN);
    umask(077);
    if (!OPENSSL_init_crypto(OPENSSL_INIT_NO_LOAD_CONFIG, NULL)) return 1;
#ifndef AW_TEST
    if (geteuid() != 0) { fputs("需要root权限\n", stderr); return 1; }
    if (setenv("PATH", "/usr/sbin:/usr/bin:/sbin:/bin", 1)) return 1;
#else
    if (argc == 3 && !strcmp(argv[1], "--test-imei")) {
        char password[9];
        if (derive(argv[2], password)) return 1;
        puts(password); return 0;
    }
#endif
    if (argc == 2 && !strcmp(argv[1], "--key-id")) { puts(key_id); return 0; }
    if (argc == 2 && !strcmp(argv[1], "--print")) {
        char imei[16], password[9];
        if (read_imei(imei) || derive(imei, password)) return 1;
        puts(password); OPENSSL_cleanse(password, sizeof password); return 0;
    }
    if (argc == 2 && !strcmp(argv[1], "--init")) return initialize(0);
    if (argc == 2 && !strcmp(argv[1], "--repair")) return initialize(1);
    if (argc == 2 && !strcmp(argv[1], "--verify-current")) {
        char imei[16], password[9];
        if (read_imei(imei) || derive(imei, password)) return 1;
        int matched = verify_password(password) == 0;
        OPENSSL_cleanse(password, sizeof password);
        puts(matched ? "当前root密码与GUI派生密码一致" : "当前root密码与GUI派生密码不一致或无法核验");
        return matched ? 0 : 3;
    }
    fputs("用法: aw1000-board-state --init|--repair|--verify-current|--print|--key-id\n", stderr);
    return 1;
}
'''

