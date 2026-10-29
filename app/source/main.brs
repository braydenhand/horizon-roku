sub Main(args as Dynamic)
    screen = CreateObject("roSGScreen")
    port = CreateObject("roMessagePort")
    screen.SetMessagePort(port)
    ' Developer hook: /launch/dev?mode=screensaver runs the screensaver scene as
    ' an app, so it can be screenshotted and debugged like one
    sceneName = "HomeScene"
    if args <> invalid and args.mode = "screensaver" then sceneName = "ScreensaverScene"
    scene = screen.CreateScene(sceneName)
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

' Screensaver entry point. It's a private screensaver (see the manifest): Roku
' runs it while Horizon is open
sub RunScreenSaver()
    screen = CreateObject("roSGScreen")
    port = CreateObject("roMessagePort")
    screen.SetMessagePort(port)
    scene = screen.CreateScene("ScreensaverScene")
    screen.Show()

    ' Fetch here rather than in Task nodes: once a screensaver over Horizon has
    ' been dismissed, Task threads in the next one never run (black screen)
    data = ParseJson(ReadAsciiFile("pkg:/data/apps.json"))
    relay = ""
    if data <> invalid and data.relay <> invalid then relay = data.relay
    scene.observeField("needPhotos", port)
    if relay = "" then
        scene.photoData = "{}"  ' no Pi: the frame shows the bundled photo
    else
        scene.weatherData = httpGet(relay + "/weather")
        scene.photoData = httpGet(relay + "/photos?auto=1")
    end if

    sinceWeather = CreateObject("roTimespan")
    while true
        msg = wait(60000, port)
        if type(msg) = "roSGScreenEvent" and msg.IsScreenClosed() then return
        if relay <> "" then
            if type(msg) = "roSGNodeEvent" and msg.GetField() = "needPhotos" then
                scene.photoData = httpGet(relay + "/photos?auto=1")
            end if
            if sinceWeather.TotalSeconds() >= 15 * 60 then
                sinceWeather.Mark()
                scene.weatherData = httpGet(relay + "/weather")
            end if
        end if
    end while
end sub

' GET with a timeout; "{}" on any failure so the receiver always updates
function httpGet(url as String) as String
    xfer = CreateObject("roUrlTransfer")
    port = CreateObject("roMessagePort")
    xfer.SetMessagePort(port)
    xfer.SetUrl(url)
    if xfer.AsyncGetToString() then
        msg = wait(8000, port)
        if type(msg) = "roUrlEvent" and msg.GetResponseCode() = 200 then return msg.GetString()
        xfer.AsyncCancel()
    end if
    return "{}"
end function
