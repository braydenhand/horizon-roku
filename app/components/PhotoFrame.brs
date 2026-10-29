' Photo frame: photos from the watcher on the Pi, crossfading with a short settle,
' plus a small clock, date and weather. Falls back to the bundled wallpaper
' when the watcher can't be reached.

sub init()
    m.photoA = m.top.findNode("photoA")
    m.photoB = m.top.findNode("photoB")
    m.overlay = m.top.findNode("overlay")
    m.clock = m.top.findNode("clock")
    m.info = m.top.findNode("info")
    m.fade = m.top.findNode("fade")
    m.fadeTo = m.top.findNode("fadeTo")
    m.zoom = { photoA: m.top.findNode("zoomA"), photoB: m.top.findNode("zoomB") }
    m.photoA.observeField("loadStatus", "onLoadStatus")
    m.photoB.observeField("loadStatus", "onLoadStatus")

    data = ParseJson(ReadAsciiFile("pkg:/data/apps.json"))
    m.base = ""
    if data <> invalid and data.relay <> invalid then m.base = data.relay
    m.fallback = "pkg:/images/wallpaper.jpg"
    m.photos = []
    m.index = -1
    m.front = invalid     ' poster currently shown
    m.loading = invalid   ' poster loading the next photo
    m.failures = 0
    m.weatherText = ""
    m.tasks = {}

    m.clockTimer = m.top.findNode("clockTimer")
    m.clockTimer.observeField("fire", "updateClock")
    m.clockTimer.control = "start"
    m.photoTimer = m.top.findNode("photoTimer")
    m.photoTimer.observeField("fire", "nextPhoto")
    m.weatherTimer = m.top.findNode("weatherTimer")
    m.timers = [m.clockTimer, m.photoTimer, m.weatherTimer]
    m.weatherTimer.observeField("fire", "fetchWeather")
    updateClock()

    ' Decide how to get data once fields set in XML or by the creator have
    ' arrived (an "external" frame is fed by its owner instead of fetching)
    m.startTimer = m.top.findNode("startTimer")
    m.startTimer.observeField("fire", "start")
    m.startTimer.control = "start"
end sub

sub start()
    if m.top.external then return  ' wait for photoData / weatherData
    if m.base = "" then
        setPhotos([])
    else
        requestPhotos()
        fetchWeather()
        m.weatherTimer.control = "start"
    end if
end sub

sub requestPhotos()
    if m.top.external then
        m.top.needPhotos = true
    else
        fetch("photos", m.base + "/photos?auto=1")
    end if
end sub

' ---------------------------------------------------------------- data

sub fetch(kind as String, url as String)
    task = CreateObject("roSGNode", "FetchTask")
    task.kind = kind
    task.url = url
    task.observeField("done", "onFetched")
    m.tasks[kind] = task  ' keep a reference until it finishes
    task.control = "run"
end sub

sub fetchWeather()
    fetch("weather", m.base + "/weather")
    ' If the Pi was unreachable earlier we are stuck on the fallback; try again
    if m.photos.Count() = 1 and m.photos[0] = m.fallback then requestPhotos()
end sub

sub onFetched(event as Object)
    task = event.getRoSGNode()
    result = invalid
    if task.code = 200 then result = ParseJson(task.response)
    if task.kind = "photos" then handlePhotos(result)
    if task.kind = "weather" then handleWeather(result)
end sub

' External mode: the owner passes the raw JSON answers
sub onPhotoData()
    handlePhotos(ParseJson(m.top.photoData))
end sub

sub onWeatherData()
    handleWeather(ParseJson(m.top.weatherData))
end sub

sub handlePhotos(result as Dynamic)
    urls = []
    if result <> invalid and result.photos <> invalid then
        for each path in result.photos
            urls.Push(m.base + path + "?size=home")
        end for
    end if
    setPhotos(urls)
end sub

sub handleWeather(result as Dynamic)
    m.weatherText = ""
    if result <> invalid and result.temp <> invalid then
        m.weatherText = result.temp.ToStr() + "°  " + result.text
    end if
    updateClock()
end sub

sub setPhotos(urls as Object)
    if urls.Count() = 0 then urls = [m.fallback]
    shuffle(urls)
    ' Start on the photo Home was showing, so the glass dissolves over the same scene
    if m.top.first <> "" then
        wanted = "/photo/" + m.top.first.EncodeUriComponent() + "?size=home"
        for i = 0 to urls.Count() - 1
            if Right(urls[i], Len(wanted)) = wanted then
                tmp = urls[0]
                urls[0] = urls[i]
                urls[i] = tmp
                exit for
            end if
        end for
        m.top.first = ""
    end if
    ' Avoid repeating the photo on screen when a new cycle starts
    if m.photos.Count() > 0 and urls.Count() > 1 and urls[0] = m.photos[m.index] then
        urls.Push(urls.Shift())
    end if
    m.photos = urls
    m.index = -1
    m.failures = 0
    nextPhoto()
    if urls.Count() > 1 then m.photoTimer.control = "start"
end sub

sub shuffle(items as Object)
    for i = items.Count() - 1 to 1 step -1
        j = Rnd(i + 1) - 1
        tmp = items[i]
        items[i] = items[j]
        items[j] = tmp
    end for
end sub

' ---------------------------------------------------------------- photos

sub nextPhoto()
    if m.photos.Count() = 0 then return
    if m.index + 1 >= m.photos.Count() and m.base <> "" and m.index >= 0 then
        ' End of a cycle: pick up photos added or removed on the Pi
        requestPhotos()
        return
    end if
    m.index = m.index + 1
    target = m.photoB
    if m.front <> invalid and m.front.id = "photoB" then target = m.photoA
    m.loading = target
    url = m.photos[m.index]
    if target.uri = url and target.loadStatus = "ready" then
        showLoaded(target)
    else
        target.uri = url
    end if
end sub

sub onLoadStatus(event as Object)
    poster = event.getRoSGNode()
    if m.loading = invalid or poster.id <> m.loading.id then return
    status = poster.loadStatus
    if status = "ready" then
        m.failures = 0
        showLoaded(poster)
    else if status = "failed" then
        print "[screensaver] failed to load "; poster.uri
        m.failures = m.failures + 1
        if m.failures >= m.photos.Count() then
            m.photos = [m.fallback]
            m.index = -1
            m.photoTimer.control = "stop"
        end if
        nextPhoto()
    end if
end sub

sub showLoaded(poster as Object)
    m.loading = invalid
    ' Settle from slightly zoomed in, toward a point near the middle
    poster.scaleRotateCenter = [800 + Rnd(384), 450 + Rnd(216)]
    poster.scale = [1.04, 1.04]
    zoom = m.zoom[poster.id]
    zoom.control = "stop"
    zoom.control = "start"

    ' photoB sits on top: fade it in to show it, or out to reveal photoA
    m.fade.control = "stop"
    if poster.id = "photoB" then
        m.fadeTo.keyValue = [m.photoB.opacity, 1.0]
    else
        m.fadeTo.keyValue = [m.photoB.opacity, 0.0]
    end if
    m.fade.control = "start"
    m.front = poster
    drift()
end sub

sub drift()
    ' Nudge the overlay a little with each photo
    m.overlay.translation = [Rnd(41) - 21, Rnd(25) - 13]
end sub

' ---------------------------------------------------------------- clock

sub updateClock()
    dt = CreateObject("roDateTime")
    dt.ToLocalTime()
    h12 = dt.GetHours() mod 12
    if h12 = 0 then h12 = 12
    m.clock.text = h12.ToStr() + ":" + Right("0" + dt.GetMinutes().ToStr(), 2)

    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    text = dt.GetWeekday() + ", " + months[dt.GetMonth() - 1] + " " + dt.GetDayOfMonth().ToStr()
    if m.weatherText <> "" then text = text + "  ·  " + m.weatherText
    m.info.text = text
end sub

sub onActive()
    if m.top.active then return
    for each timer in m.timers
        timer.control = "stop"
    end for
end sub
