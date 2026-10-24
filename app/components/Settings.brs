' Two pages: setting rows (Up/Down, OK opens the picker, Left/Right changes the
' rotation) and the wallpaper picker (a grid: "Automatic" then every photo on
' the Pi). Choices are saved to the registry and reported through "changed".

sub init()
    for each id in ["backdrop", "title", "subtitle", "rows", "rowPlate", "picker", "cells", "pickPlate", "message", "rowAnim", "rowTo", "pickAnim", "pickTo", "scrollAnim", "scrollTo"]
        m[id] = m.top.findNode(id)
    end for
    m.rotations = [
        { value: "open", label: "Each time Home opens" },
        { value: "15", label: "Every 15 minutes" },
        { value: "60", label: "Every hour" }
    ]
    m.rowH = 128
    m.cols = 4
    m.cellW = 416
    m.cellH = 268
    m.pickLeft = 128
    m.pickTop = 232
    m.picker.translation = [m.pickLeft, m.pickTop]
    m.picker.clippingRect = [-m.pickLeft, -24, 1920, 1080 - m.pickTop + 24]
    m.rowsVisible = Int((1080 - m.pickTop) / m.cellH)

    m.reg = CreateObject("roRegistrySection", "home")
    m.pin = m.reg.Read("wallPin")
    m.every = m.reg.Read("wallEvery")
    if m.every = "" then m.every = "open"
    m.gallery = invalid
    m.pinTitle = ""
    m.labels = []
    m.topRow = 0
    m.pick = 0
    buildRows()
    m.isOpen = false
end sub

sub onPhoto()
    if m.top.relay <> "" and m.top.photo <> "" then
        m.backdrop.uri = m.top.relay + "/blur/" + m.top.photo.EncodeUriComponent()
    else
        m.backdrop.uri = "pkg:/images/wallpaper_blur.jpg"
    end if
end sub

sub onOpened()
    m.isOpen = true
    onPhoto()
    showRows()
end sub

' ---------------------------------------------------------------- rows

function makeLabel(parent as Object, size as Integer, color as String) as Object
    font = CreateObject("roSGNode", "Font")
    font.uri = "pkg:/fonts/Inter-Medium.ttf"
    font.size = size
    label = parent.createChild("Label")
    label.font = font
    label.color = color
    return label
end function

sub buildRows()
    m.rowNodes = []
    for each name in ["Wallpaper", "Change photo"]
        row = m.rows.createChild("Group")
        row.translation = [0, m.rowNodes.Count() * m.rowH]
        title = makeLabel(row, 40, "0xF6F1E7FF")
        title.text = name
        title.translation = [32, 14]
        value = makeLabel(row, 26, "0xF6F1E7B8")
        value.translation = [32, 66]
        m.rowNodes.Push({ group: row, value: value })
    end for
    m.row = 0
end sub

sub showRows()
    m.page = "rows"
    m.title.text = "Settings"
    m.subtitle.text = ""
    m.picker.visible = false
    m.message.text = ""
    m.rows.visible = true
    updateValues()
end sub

sub updateValues()
    if m.pin = "" then
        m.rowNodes[0].value.text = "Automatic  ·  brighter by day, darker at night"
    else
        title = m.pinTitle
        if title = "" then title = m.pin
        m.rowNodes[0].value.text = title
    end if
    label = m.rotations[0].label
    for each r in m.rotations
        if r.value = m.every then label = r.label
    end for
    if m.pin <> "" then
        m.rowNodes[1].value.text = "Off while a photo is chosen"
    else if m.row = 1 then
        m.rowNodes[1].value.text = "◀  " + label + "  ▶"
    else
        m.rowNodes[1].value.text = label
    end if
end sub

sub moveRow(delta as Integer)
    target = m.row + delta
    if target < 0 or target >= m.rowNodes.Count() then return
    m.row = target
    m.rowAnim.control = "stop"
    m.rowTo.keyValue = [m.rowPlate.translation, [0, m.row * m.rowH]]
    m.rowAnim.control = "start"
    updateValues()
end sub

sub cycleRotation(delta as Integer)
    if m.pin <> "" then return
    index = 0
    for i = 0 to m.rotations.Count() - 1
        if m.rotations[i].value = m.every then index = i
    end for
    index = (index + delta + m.rotations.Count()) mod m.rotations.Count()
    m.every = m.rotations[index].value
    save()
    updateValues()
end sub

sub save()
    m.reg.Write("wallPin", m.pin)
    m.reg.Write("wallEvery", m.every)
    m.reg.Flush()
    m.top.changed = { pin: m.pin, every: m.every }
end sub

' ---------------------------------------------------------------- picker

sub showPicker()
    m.page = "picker"
    m.rows.visible = false
    m.picker.visible = true
    m.title.text = "Wallpaper"
    m.subtitle.text = "Choose a photo, or Automatic to change by time of day"
    if m.gallery = invalid then
        m.message.text = "Loading photos…"
        m.task = CreateObject("roSGNode", "FetchTask")
        m.task.url = m.top.relay + "/gallery"
        m.task.observeField("done", "onGallery")
        m.task.control = "run"
    else
        focusPick(selectedIndex(), false)
    end if
end sub

sub onGallery()
    result = invalid
    if m.task.code = 200 then result = ParseJson(m.task.response)
    if result = invalid or result.photos = invalid then
        m.message.text = "Couldn't get the photos from the Pi."
        return
    end if
    m.message.text = ""
    ' The first choice is Automatic
    m.gallery = [{ name: "", title: "Automatic", thumb: "" }]
    m.gallery.Append(result.photos)
    m.labels = []
    for i = 0 to m.gallery.Count() - 1
        makePick(i, m.gallery[i])
        if m.gallery[i].name = m.pin and m.pin <> "" then m.pinTitle = m.gallery[i].title
    end for
    updateChecks()
    m.topRow = 0
    focusPick(selectedIndex(), false)
end sub

sub makePick(i as Integer, item as Object)
    cell = m.cells.createChild("Group")
    cell.translation = pickPosition(i, 0)
    thumb = cell.createChild("Poster")
    thumb.translation = [48, 20]
    thumb.width = 320
    thumb.height = 180
    if item.thumb = "" then
        thumb.uri = "pkg:/images/thumb_auto.png"
        auto = makeLabel(cell, 30, "0xF6F1E7FF")
        auto.text = "Day & night"
        auto.width = 320
        auto.height = 180
        auto.translation = [48, 20]
        auto.horizAlign = "center"
        auto.vertAlign = "center"
    else
        thumb.uri = m.top.relay + item.thumb
    end if
    label = makeLabel(cell, 26, "0xF6F1E7FF")
    label.width = m.cellW - 16
    label.translation = [8, 214]
    label.horizAlign = "center"
    m.labels.Push(label)
end sub

function pickPosition(i as Integer, inset as Integer) as Object
    return [(i mod m.cols) * m.cellW + inset, Int(i / m.cols) * m.cellH + inset]
end function

function selectedIndex() as Integer
    if m.gallery = invalid then return 0
    for i = 0 to m.gallery.Count() - 1
        if m.gallery[i].name = m.pin then return i
    end for
    return 0
end function

sub updateChecks()
    for i = 0 to m.labels.Count() - 1
        text = m.gallery[i].title
        if text = "" then text = m.gallery[i].name
        if m.gallery[i].name = m.pin then text = "✓  " + text
        m.labels[i].text = text
    end for
end sub

sub focusPick(i as Integer, animate as Boolean)
    m.pick = i
    target = pickPosition(i, 8)
    m.pickPlate.width = m.cellW - 16
    m.pickPlate.height = m.cellH - 16
    if animate then
        m.pickAnim.control = "stop"
        m.pickTo.keyValue = [m.pickPlate.translation, target]
        m.pickAnim.control = "start"
    else
        m.pickPlate.translation = target
    end if
    row = Int(i / m.cols)
    topRow = m.topRow
    if row < topRow then topRow = row
    if row > topRow + m.rowsVisible - 1 then topRow = row - m.rowsVisible + 1
    if topRow <> m.topRow then
        m.topRow = topRow
        m.scrollAnim.control = "stop"
        m.scrollTo.keyValue = [m.cells.translation, [0, -topRow * m.cellH]]
        m.scrollAnim.control = "start"
    end if
end sub

sub movePick(delta as Integer)
    if m.gallery = invalid then return
    target = m.pick + delta
    if target < 0 or target >= m.gallery.Count() then return
    if (delta = 1 or delta = -1) and Int(target / m.cols) <> Int(m.pick / m.cols) then return
    focusPick(target, true)
end sub

sub choosePick()
    if m.gallery = invalid then return
    item = m.gallery[m.pick]
    m.pin = item.name
    m.pinTitle = ""
    if m.pin <> "" then m.pinTitle = item.title
    save()
    updateChecks()
    showRows()
end sub

' ---------------------------------------------------------------- keys

function onKeyEvent(key as String, press as Boolean) as Boolean
    ' A hidden sheet must never act on keys, even if it still holds focus
    if not press or not m.isOpen then return false
    if m.page = "rows" then
        if key = "up" then moveRow(-1)
        if key = "down" then moveRow(1)
        if key = "left" and m.row = 1 then cycleRotation(-1)
        if key = "right" and m.row = 1 then cycleRotation(1)
        if key = "OK" and m.row = 0 then showPicker()
        if key = "OK" and m.row = 1 then cycleRotation(1)
        if key = "back" then
            m.isOpen = false
            m.top.closed = true
        end if
    else
        if key = "right" then movePick(1)
        if key = "left" then movePick(-1)
        if key = "down" then movePick(m.cols)
        if key = "up" then movePick(-m.cols)
        if key = "OK" then choosePick()
        if key = "back" then showRows()
    end if
    return true
end function

' Free the backdrop and picker thumbnails while hidden (see AllApps.release)
sub release()
    m.backdrop.uri = ""
    cells = m.cells.getChildren(-1, 0)
    for each cell in cells
        if cell.id <> "pickPlate" then m.cells.removeChild(cell)
    end for
    m.gallery = invalid
    m.labels = []
    m.topRow = 0
    m.pick = 0
    m.cells.translation = [0, 0]
end sub
