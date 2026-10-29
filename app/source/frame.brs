' Helpers shared by the photo frame (PhotoFrame) and the Roku screensaver
' (screensaver.brs)

function clockText() as String
    dt = CreateObject("roDateTime")
    dt.ToLocalTime()
    h12 = dt.GetHours() mod 12
    if h12 = 0 then h12 = 12
    return h12.ToStr() + ":" + Right("0" + dt.GetMinutes().ToStr(), 2)
end function

' "Thursday, October 29  ·  80°  Cloudy"
function infoText(weatherText as String) as String
    dt = CreateObject("roDateTime")
    dt.ToLocalTime()
    months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"]
    text = dt.GetWeekday() + ", " + months[dt.GetMonth() - 1] + " " + dt.GetDayOfMonth().ToStr()
    if weatherText <> "" then text = text + "  ·  " + weatherText
    return text
end function

sub shuffle(items as Object)
    for i = items.Count() - 1 to 1 step -1
        j = Rnd(i + 1) - 1
        tmp = items[i]
        items[i] = items[j]
        items[j] = tmp
    end for
end sub
