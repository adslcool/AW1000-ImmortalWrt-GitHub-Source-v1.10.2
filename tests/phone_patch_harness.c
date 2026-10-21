#include <assert.h>
#include <stdint.h>
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include "patch.h"
int main(int argc, char **argv) {
    assert(argc == 2);
    FILE *f = fopen(argv[1], "rb"); assert(f);
    uint8_t b[909]; size_t n = fread(b, 1, sizeof(b), f); fclose(f);
    assert(n == 908);
    struct si_patch p;
    assert(si_patch_parse(b, n, &p) == 0);
    assert(p.serial == 0x05262017 && p.n_data == 191 && p.n_ps == 16);
    assert(p.data[190] == 0 && p.entries[0] == 950 && p.entries[9] == 4006);
    assert(p.ps_addr[6] == 893 && p.ps_data[6] == 0);
    assert(p.ps_addr[15] == 352 && p.ps_data[15] == 0);
    si_patch_free(&p);
    assert(si_patch_parse(b, n-1, &p) != 0);
    b[n] = 0; assert(si_patch_parse(b, n+1, &p) != 0);
    b[0] = 'X'; assert(si_patch_parse(b, n, &p) != 0);
    puts("PASS: real SLIC parser accepts patch and zero RAM values; rejects malformed data");
    return 0;
}
