' Horizon's screensaver. Everything runs on this thread, driving the nodes of
' ScreensaverScene (see the note there for why it can't use component code).
' Behaves like PhotoFrame: photos from the Pi crossfading every minute with a
' short settle, plus clock, date and weather.

sub RunScreenSaver()
    screen = CreateObject("roSGScreen")
    port = CreateObject("roMessagePort")
    screen.SetMessagePort(port)
    scene = screen.CreateScene("ScreensaverScene")
    screen.Show()

    s = {
        photoA: scene.findNode("photoA")
        photoB: scene.findNode("photoB")
        overlay: scene.findNode("overlay")
        clock: scene.findNode("clock")
        info: scene.findNode("info")
        fade: scene.findNode("fade")
        fadeTo: scene.findNode("fadeTo")
        zoom: { photoA: scene.findNode("zoomA"), photoB: scene.findNode("zoomB") }
        base: ""
        fallback: "pkg:/images/wallpaper.jpg"
        photos: []
        index: -1
        front: invalid    ' poster currently shown
        loading: invalid  ' poster loading the next photo
        failures: 0
        weatherText: ""
    }
    s.photoA.observeField("loadStatus", port)
    s.photoB.observeField("loadStatus", port)
    data = ParseJson(ReadAsciiFile("pkg:/data/apps.json"))
    if data <> invalid and data.relay <> invalid then s.base = data.relay

    saverWeather(s)
    saverSetClock(s)
    saverLoadPhotos(s)

    sincePhoto = CreateObject("roTimespan")
    sinceWeather = CreateObject("roTimespan")
    while true
        msg = wait(5000, port)
        if type(msg) = "roSGScreenEvent" and msg.IsScreenClosed() then return
        if type(msg) = "roSGNodeEvent" and msg.GetField() = "loadStatus" then
            saverOnLoadStatus(s, msg.GetRoSGNode())
        end if
        if sincePhoto.TotalSeconds() >= 60 and s.photos.Count() > 1 then
            sincePhoto.Mark()
            saverNextPhoto(s)
        end if
        if sinceWeather.TotalSeconds() >= 15 * 60 then
            sinceWeather.Mark()
            saverWeather(s)
            ' Stuck on the fallback because the Pi was unreachable: try again
            if s.photos.Count() = 1 and s.photos[0] = s.fallback then saverLoadPhotos(s)
        end if
        saverSetClock(s)
    end while
end sub

sub saverSetClock(s as Object)
    s.clock.text = clockText()
    s.info.text = infoText(s.weatherText)
end sub

sub saverWeather(s as Object)
    if s.base = "" then return
    result = ParseJson(httpGet(s.base + "/weather"))
    s.weatherText = ""
    if result <> invalid and result.temp <> invalid then
        s.weatherText = result.temp.ToStr() + "°  " + result.text
    end if
end sub

' Fetch the photo list and start a new shuffled cycle
sub saverLoadPhotos(s as Object)
    urls = []
    if s.base <> "" then
        result = ParseJson(httpGet(s.base + "/photos?auto=1"))
        if result <> invalid and result.photos <> invalid then
            for each path in result.photos
                urls.Push(s.base + path + "?size=home")
            end for
        end if
    end if
    if urls.Count() = 0 then urls = [s.fallback]
    shuffle(urls)
    ' Avoid repeating the photo on screen when a new cycle starts
    if s.index >= 0 and urls.Count() > 1 and urls[0] = s.photos[s.index] then urls.Push(urls.Shift())
    s.photos = urls
    s.index = -1
    s.failures = 0
    saverNextPhoto(s)
end sub

sub saverNextPhoto(s as Object)
    if s.index + 1 >= s.photos.Count() and s.base <> "" and s.index >= 0 then
        ' End of a cycle: pick up photos added or removed on the Pi
        saverLoadPhotos(s)
        return
    end if
    s.index = s.index + 1
    target = s.photoB
    if s.front <> invalid and s.front.id = "photoB" then target = s.photoA
    s.loading = target
    url = s.photos[s.index]
    if target.uri = url and target.loadStatus = "ready" then
        saverShow(s, target)
    else
        target.uri = url
    end if
end sub

sub saverOnLoadStatus(s as Object, poster as Object)
    if s.loading = invalid or poster.id <> s.loading.id then return
    status = poster.loadStatus
    if status = "ready" then
        s.failures = 0
        saverShow(s, poster)
    else if status = "failed" then
        print "[screensaver] failed to load "; poster.uri
        s.failures = s.failures + 1
        if s.failures >= s.photos.Count() then
            s.photos = [s.fallback]
            s.index = -1
        end if
        saverNextPhoto(s)
    end if
end sub

sub saverShow(s as Object, poster as Object)
    s.loading = invalid
    ' Settle from slightly zoomed in, toward a point near the middle
    poster.scaleRotateCenter = [800 + Rnd(384), 450 + Rnd(216)]
    poster.scale = [1.04, 1.04]
    zoom = s.zoom[poster.id]
    zoom.control = "stop"
    zoom.control = "start"
    ' photoB sits on top: fade it in to show it, or out to reveal photoA
    s.fade.control = "stop"
    if poster.id = "photoB" then
        s.fadeTo.keyValue = [s.photoB.opacity, 1.0]
    else
        s.fadeTo.keyValue = [s.photoB.opacity, 0.0]
    end if
    s.fade.control = "start"
    s.front = poster
    ' Nudge the overlay a little with each photo
    s.overlay.translation = [Rnd(41) - 21, Rnd(25) - 13]
end sub

' GET with a timeout; "{}" on any failure
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
