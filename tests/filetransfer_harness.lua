-- Execute the real LuCI model against a small boundary double. Package install
-- commands run through a real shell and dummy package managers on PATH.
local has_apk = arg[2] == "apk"
local entries = { "safe.apk", "bad ' $(touch INJECTED).apk", "legacy.ipk" }
local forms = {}
local upload_handler
FileUpload = {}
DummyValue = {}
Table = {}
SimpleSection = {}
Button = { render = function() end }
translate = function(value) return value end
nixio = {}

function SimpleForm()
    local form = { sections = {} }
    function form:section()
        local section = { options = {} }
        function section:option(kind, key)
            local option = {}
            self.options[key] = option
            return option
        end
        table.insert(self.sections, section)
        return section
    end
    table.insert(forms, form)
    return form
end

package.preload["nixio.fs"] = function()
    return {
        access = function(path) return has_apk and path == "/usr/bin/apk" end,
        mkdir = function() end,
        basename = function(path) return path:match("([^/]+)$") or "" end,
        lstat = function(path, key)
            local attr = {type="reg", mtime=0, modestr="rw-------", size=12}
            return key and attr[key] or attr
        end,
        glob = function()
            local index = 0
            return function()
                index = index + 1
                return entries[index] and "/tmp/upload/" .. entries[index]
            end
        end
    }
end
package.preload["luci.http"] = function()
    local parsed = false
    return {formvalue=function(key)
        if arg[3] == "upload_submit" and not parsed then
            parsed = true
            nixio.open = function()
                return {writeall=function() end, close=function() end}
            end
            upload_handler({file="test.txt"},"content",true)
        end
        if (arg[3] == "upload_submit" or arg[3] == "empty_submit") and key == "upload" then
            return "Upload"
        end
        -- File content is consumed by the handler, not a scalar ulfile value.
        return nil
    end, setfilehandler=function(handler) upload_handler=handler end}
end
package.preload["luci.util"] = function()
    return {
        shellquote=function(value) return "'" .. value:gsub("'", "'\\''") .. "'" end,
        pcdata=function(value) return value end
    }
end
package.preload["luci.sys"] = function()
    return {exec=function(command)
        local pipe = assert(io.popen(command, "r"))
        local result = pipe:read("*a")
        pipe:close()
        return result
    end}
end

assert(loadfile(arg[1]))()
if arg[3] == "upload_submit" or arg[3] == "empty_submit" then
    local message = forms[1].sections[1].options[""].value
    if arg[3] == "upload_submit" then
        assert(message == 'File saved to "/tmp/upload/test.txt"', tostring(message))
    else
        assert(message == "No specify upload file.", tostring(message))
    end
    print("PASS: multipart upload result message")
    return
end
if arg[3] == "upload" then
    local written, closed, opens = "", false, 0
    nixio.open = function(path, flags, mode)
        assert(type(mode) == "string" and mode == "600", "nixio permissions must use supported octal string")
        assert(path == "/tmp/upload/上传测试.txt" and flags == "w")
        opens = opens + 1
        return {
            writeall=function(self,chunk) written=written..chunk; return #chunk end,
            close=function() closed=true end
        }
    end
    assert(upload_handler, "real upload callback was not registered")
    upload_handler({file="../上传测试.txt"},"head\000",false)
    upload_handler({file="../上传测试.txt"},"tail",true)
    assert(written == "head\000tail" and closed and opens == 1)
    upload_handler({file="bad\nname"},"ignored",true)
    assert(opens == 1, "control character filename was not rejected")
    local fs=require "nixio.fs"
    local old_lstat=fs.lstat
    fs.lstat=function(path,key) if path=="/tmp/upload/link.txt" then return "lnk" else return old_lstat(path,key) end end
    upload_handler({file="link.txt"},"ignored",true)
    assert(opens == 1, "symlink upload target was not rejected")
    print("PASS: real upload callback, binary chunks, octal permissions, basename and rejected targets")
    return
end
local install = forms[3].sections[1].options.install
-- Sorted order is bad..., legacy..., safe...
for index = 1, 3 do
    local scope = {}
    install:render(index, scope)
    assert(scope.display == ((has_apk and index ~= 2 or not has_apk and index == 2) and "" or "none"))
    -- Even an unexpected callback for an incompatible extension must do nothing.
    install:write(index)
end
