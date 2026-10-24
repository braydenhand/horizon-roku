' HTTP GET in a task thread. Sets code and response, then done.

sub init()
    m.top.functionName = "run"
end sub

sub run()
    xfer = CreateObject("roUrlTransfer")
    port = CreateObject("roMessagePort")
    xfer.SetMessagePort(port)
    xfer.SetUrl(m.top.url)
    code = -1
    body = ""
    if xfer.AsyncGetToString() then
        msg = wait(10000, port)
        if type(msg) = "roUrlEvent" then
            code = msg.GetResponseCode()
            body = msg.GetString()
        else
            xfer.AsyncCancel()
            code = -2
        end if
    end if
    m.top.code = code
    m.top.response = body
    m.top.done = true
end sub
