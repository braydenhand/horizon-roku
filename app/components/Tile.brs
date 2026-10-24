' Focus brightens the tile and lifts it 3px; picking it up for moving scales it
' up. The gliding plate behind it is drawn by HomeScene.

sub init()
    for each id in ["slot", "body", "mark", "label", "name", "anim", "fadeTo", "liftTo", "scaleTo", "slide", "slideTo"]
        m[id] = m.top.findNode(id)
    end for
    m.placed = false
end sub

sub onApp()
    app = m.top.app
    if app.mark <> invalid then
        ' Marks are centered in a 132x64 box near the top of the 288x200 tile
        m.mark.uri = app.mark
        m.mark.width = app.markW
        m.mark.height = app.markH
        m.mark.translation = [Int((288 - app.markW) / 2), 34 + Int((64 - app.markH) / 2)]
        m.label.text = app.name
        m.mark.visible = true
        m.label.visible = true
        m.name.visible = false
    else
        m.name.text = app.name
        m.name.visible = true
        m.mark.visible = false
        m.label.visible = false
    end if
end sub

sub onState()
    opacity = 0.84
    lift = 0
    scale = 1.0
    if m.top.highlighted or m.top.lifted then
        opacity = 1.0
        lift = -3
    end if
    if m.top.lifted then scale = 1.08
    m.anim.control = "stop"
    m.fadeTo.keyValue = [m.body.opacity, opacity]
    m.liftTo.keyValue = [m.body.translation, [0, lift]]
    m.scaleTo.keyValue = [m.body.scale, [scale, scale]]
    m.anim.control = "start"
end sub

sub onSlotX()
    target = [m.top.slotX, 0]
    if not m.placed then
        ' First placement: no animation
        m.placed = true
        m.slot.translation = target
        return
    end if
    m.slide.control = "stop"
    m.slideTo.keyValue = [m.slot.translation, target]
    m.slide.control = "start"
end sub
