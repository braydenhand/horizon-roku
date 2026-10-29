sub Main(args as Dynamic)
    ' Developer hook: /launch/dev?mode=screensaver runs the screensaver as an
    ' app, so it can be screenshotted and debugged like one
    if args <> invalid and args.mode = "screensaver" then
        RunScreenSaver()
        return
    end if

    screen = CreateObject("roSGScreen")
    port = CreateObject("roMessagePort")
    screen.SetMessagePort(port)
    scene = screen.CreateScene("HomeScene")
    screen.Show()
    if args <> invalid and args.reason <> invalid then scene.launchReason = args.reason

    ' Deep links sent while we are already running (e.g. /launch/dev?reason=poweron)
    input = CreateObject("roInput")
    input.SetMessagePort(port)

    while true
        msg = wait(0, port)
        if type(msg) = "roSGScreenEvent" and msg.IsScreenClosed() then return
        if type(msg) = "roInputEvent" then
            info = msg.GetInfo()
            if info.reason <> invalid then scene.launchReason = info.reason
        end if
    end while
end sub
