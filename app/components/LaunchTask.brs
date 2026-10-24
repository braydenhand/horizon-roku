' Opens another app. roAppManager.LaunchApp handles installed apps. TV inputs
' (tvinput.*) are refused by LaunchApp, and the TV blocks ECP requests from
' itself, so those go through the watcher's launch relay on another machine.

sub init()
    m.top.functionName = "run"
end sub

sub run()
    appId = m.top.appId
    am = CreateObject("roAppManager")
    if FindMemberFunction(am, "LaunchApp") <> invalid then
        ok = am.LaunchApp(appId, "", {})
        print "[home] roAppManager.LaunchApp "; appId; " -> "; ok
        if ok = true then
            m.top.result = 200
            return
        end if
    end if

    if m.top.relay = "" then
        m.top.result = -3
        return
    end if
    m.top.result = post(m.top.relay + "/launch/" + appId)
end sub

function post(url as String) as Integer
    xfer = CreateObject("roUrlTransfer")
    port = CreateObject("roMessagePort")
    xfer.SetMessagePort(port)
    xfer.SetUrl(url)
    xfer.RetainBodyOnError(true)
    if not xfer.AsyncPostFromString("") then return -1
    msg = wait(6000, port)
    if type(msg) <> "roUrlEvent" then
        xfer.AsyncCancel()
        print "[home] POST "; url; " -> timeout"
        return -2
    end if
    print "[home] POST "; url; " -> "; msg.GetResponseCode(); " "; msg.GetFailureReason(); " body="; msg.GetString()
    return msg.GetResponseCode()
end function
