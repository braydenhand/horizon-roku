' Grid navigation: arrows move a gliding plate, the grid scrolls by rows to keep
' it in view, OK asks HomeScene to launch, Back asks it to close the sheet.

sub init()
    for each id in ["backdrop", "count", "viewport", "cells", "plate", "message", "plateAnim", "plateTo", "scrollAnim", "scrollTo"]
        m[id] = m.top.findNode(id)
    end for
    m.apps = invalid
    m.cellNodes = []
    m.labels = []
    m.noticeTimer = m.top.findNode("noticeTimer")
    m.noticeTimer.observeField("fire", "showCount")
    m.index = 0
    m.topRow = 0
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
    g = m.top.grid
    m.cols = g.cols
    m.cellW = g.cellW
    m.cellH = g.cellH
    m.viewport.translation = [g.left, g.top]
    m.viewport.clippingRect = [-g.left, -24, 1920, 1080 - g.top + 24]
    m.rowsVisible = Int((1080 - g.top) / g.cellH)
    if m.apps = invalid then
        m.message.text = "Loading apps…"
        m.task = CreateObject("roSGNode", "FetchTask")
        m.task.url = m.top.relay + "/apps"
        m.task.observeField("done", "onApps")
        m.task.control = "run"
    end if
end sub

sub onApps()
    result = invalid
    if m.task.code = 200 then result = ParseJson(m.task.response)
    if result = invalid or result.apps = invalid or result.apps.Count() = 0 then
        m.message.text = "Couldn't get the app list from the Pi."
        return
    end if
    m.message.text = ""
    m.apps = result.apps
    showCount()
    for i = 0 to m.apps.Count() - 1
        m.cellNodes.Push(makeCell(i, m.apps[i]))
    end for
    updateChecks()
    m.plate.opacity = 1.0
    m.plate.translation = cellPosition(0, 8)
    highlight(0, true)
end sub

function makeCell(i as Integer, app as Object) as Object
    cell = m.cells.createChild("Group")
    cell.translation = cellPosition(i, 0)
    cell.opacity = 0.84
    icon = cell.createChild("Poster")
    icon.translation = [24, 20]
    icon.width = 288
    icon.height = 216
    icon.uri = m.top.relay + app.icon
    font = CreateObject("roSGNode", "Font")
    font.uri = "pkg:/fonts/Inter-Medium.ttf"
    font.size = 28
    label = cell.createChild("Label")
    label.font = font
    label.text = app.name
    label.color = "0xF6F1E7FF"
    label.width = m.cellW - 16
    label.translation = [8, 250]
    label.horizAlign = "center"
    m.labels.Push(label)
    return cell
end function

function cellPosition(i as Integer, inset as Integer) as Object
    return [(i mod m.cols) * m.cellW + inset, Int(i / m.cols) * m.cellH + inset]
end function

sub highlight(i as Integer, on as Boolean)
    if on then
        m.cellNodes[i].opacity = 1.0
    else
        m.cellNodes[i].opacity = 0.84
    end if
end sub

function move(delta as Integer) as Boolean
    if m.apps = invalid then return true
    target = m.index + delta
    if target < 0 or target >= m.apps.Count() then return true
    ' Left/Right stop at the row's ends
    if (delta = 1 or delta = -1) and Int(target / m.cols) <> Int(m.index / m.cols) then return true
    highlight(m.index, false)
    m.index = target
    highlight(m.index, true)

    m.plateAnim.control = "stop"
    m.plateTo.keyValue = [m.plate.translation, cellPosition(m.index, 8)]
    m.plateAnim.control = "start"

    ' Keep the focused row within the visible rows
    row = Int(m.index / m.cols)
    topRow = m.topRow
    if row < topRow then topRow = row
    if row > topRow + m.rowsVisible - 1 then topRow = row - m.rowsVisible + 1
    if topRow <> m.topRow then
        m.topRow = topRow
        m.scrollAnim.control = "stop"
        m.scrollTo.keyValue = [m.cells.translation, [0, -topRow * m.cellH]]
        m.scrollAnim.control = "start"
    end if
    return true
end function

function onKeyEvent(key as String, press as Boolean) as Boolean
    ' A hidden sheet must never act on keys, even if it still holds focus
    if not press or not m.isOpen then return false
    if key = "right" then return move(1)
    if key = "left" then return move(-1)
    if key = "down" then
        ' From the second-to-last row, Down goes to the last app
        if m.apps <> invalid and m.index + m.cols >= m.apps.Count() and Int(m.index / m.cols) < Int((m.apps.Count() - 1) / m.cols) then
            return move(m.apps.Count() - 1 - m.index)
        end if
        return move(m.cols)
    end if
    if key = "up" then return move(-m.cols)
    if key = "options" and m.apps <> invalid then
        m.top.toggleHome = m.apps[m.index]
        return true
    end if
    if key = "OK" and m.apps <> invalid then
        m.top.launchApp = m.apps[m.index]
        return true
    end if
    if key = "back" then
        m.isOpen = false
        m.top.closed = true
        return true
    end if
    return true
end function

sub showCount()
    if m.apps = invalid then return
    m.count.text = m.apps.Count().ToStr() + " apps  ·  press * to add or remove from Home"
end sub

sub onNotice()
    m.count.text = m.top.notice
    m.noticeTimer.control = "stop"
    m.noticeTimer.control = "start"
end sub

sub updateChecks()
    if m.apps = invalid or m.top.homeIds = invalid then return
    onHome = {}
    for each id in m.top.homeIds
        onHome[id] = true
    end for
    for i = 0 to m.labels.Count() - 1
        if onHome.DoesExist(m.apps[i].id) then
            m.labels[i].text = "✓  " + m.apps[i].name
        else
            m.labels[i].text = m.apps[i].name
        end if
    end for
end sub

' The TV has ~100 MB for images and a hidden node keeps its images loaded, so
' drop the backdrop and icons when closed; they reload quickly from the Pi.
sub release()
    m.backdrop.uri = ""
    m.cells.removeChildren(m.cellNodes)
    m.cellNodes = []
    m.labels = []
    m.apps = invalid
    m.index = 0
    m.topRow = 0
    m.cells.translation = [0, 0]
    m.plate.opacity = 0
end sub
