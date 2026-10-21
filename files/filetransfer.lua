--[[
luci-app-filetransfer
Original authors: yuleniwo <xzm2@qq.com>, ayongwifi <ayongwifi@126.com>
Apache-2.0; adapted for ImmortalWrt 25.12, APK and safe filename quoting.
]]--
local fs = require "nixio.fs"
local http = require "luci.http"
local util = require "luci.util"
local sys = require "luci.sys"
local dir = "/tmp/upload/"

local ful = SimpleForm("upload", translate("Upload"), nil)
ful.reset = false
ful.submit = false
local sul = ful:section(SimpleSection, "", translate("Upload file to '/tmp/upload/'"))
local fu = sul:option(FileUpload, "")
fu.template = "filetransfer/other_upload"
local um = sul:option(DummyValue, "", nil)
um.template = "filetransfer/other_dvalue"

local fdl = SimpleForm("download", translate("Download"), nil)
fdl.reset = false
fdl.submit = false
local sdl = fdl:section(SimpleSection, "", translate("Download file"))
local fdoption = sdl:option(FileUpload, "")
fdoption.template = "filetransfer/other_download"
local dm = sdl:option(DummyValue, "", nil)
dm.template = "filetransfer/other_dvalue"

local upload_fd, upload_name
fs.mkdir(dir)
http.setfilehandler(function(meta, chunk, eof)
    if not upload_fd then
        if not meta or not meta.file or meta.file == "" then return end
        upload_name = fs.basename(meta.file)
        if upload_name == "." or upload_name == ".." or upload_name:find("[%c]") then return end
        local path = dir .. upload_name
        if fs.lstat(path, "type") == "lnk" then
            um.value = translate("Create upload file error.")
            return
        end
        upload_fd = nixio.open(path, "w", "600")
        if not upload_fd then
            um.value = translate("Create upload file error.")
            return
        end
    end
    if chunk then upload_fd:writeall(chunk) end
    if eof then
        upload_fd:close()
        upload_fd = nil
        um.value = translate("File saved to") .. ' "' .. util.pcdata(dir .. upload_name) .. '"'
    end
end)

if http.formvalue("upload") then
    local name = http.formvalue("ulfile") or ""
    -- Multipart file fields may have no scalar formvalue after the handler
    -- consumes them. Preserve the callback's success or file-open error.
    if #name == 0 and not upload_name then
        um.value = translate("No specify upload file.")
    end
elseif http.formvalue("download") then
    local path = http.formvalue("dlfile") or ""
    local name = fs.basename(path)
    local input
    if fs.stat(path, "type") == "dir" then
        input = io.popen("tar -C " .. util.shellquote(path) .. " -cz .", "r")
        name = name .. ".tar.gz"
    else
        input = nixio.open(path, "r")
    end
    if input then
        name = name:gsub('[%c"\\]', "_")
        http.header("Content-Disposition", 'attachment; filename="' .. name .. '"')
        http.prepare_content("application/octet-stream")
        while true do
            local chunk = input:read(8192)
            if not chunk or #chunk == 0 then break end
            http.write(chunk)
        end
        input:close()
        http.close()
        return
    end
    dm.value = translate("Couldn't open file: ") .. util.pcdata(path)
end

local entries = {}
for path in fs.glob(dir .. "*") do
    local attr = fs.lstat(path)
    if attr and attr.type == "reg" then
        entries[#entries + 1] = {
            name = fs.basename(path),
            mtime = os.date("%Y-%m-%d %H:%M:%S", attr.mtime),
            modestr = attr.modestr,
            size = tostring(attr.size)
        }
    end
end
table.sort(entries, function(a, b) return a.name < b.name end)
local form = SimpleForm("filelist", translate("Upload file list"), nil)
form.reset = false
form.submit = false
local tb = form:section(Table, entries)
tb:option(DummyValue, "name", translate("File name"))
tb:option(DummyValue, "mtime", translate("Modify time"))
tb:option(DummyValue, "modestr", translate("Mode string"))
tb:option(DummyValue, "size", translate("Size"))
local remove = tb:option(Button, "remove", translate("Remove"))
remove.inputstyle = "remove"
remove.write = function(self, section)
    if entries[section] then return fs.unlink(dir .. fs.basename(entries[section].name)) end
end

local has_apk = fs.access("/usr/bin/apk") or fs.access("/sbin/apk") or fs.access("/bin/apk")
local function compatible_package(name)
    local ext = string.lower(string.sub(name or "", -4))
    if has_apk then return ext == ".apk" end
    return ext == ".ipk"
end
local install = tb:option(Button, "install", translate("Install"))
install.template = "filetransfer/other_button"
install.render = function(self, section, scope)
    if not entries[section] then return false end
    scope.display = compatible_package(entries[section].name) and "" or "none"
    self.inputstyle = "apply"
    Button.render(self, section, scope)
end
install.write = function(self, section)
    if not entries[section] or not compatible_package(entries[section].name) then return end
    local path = dir .. fs.basename(entries[section].name)
    if fs.lstat(path, "type") ~= "reg" then return end
    local command = has_apk and "apk add --allow-untrusted " or "opkg install "
    local result = sys.exec(command .. util.shellquote(path) .. " 2>&1")
    form.description = "<pre>" .. util.pcdata(result) .. "</pre>"
end

return ful, fdl, form
