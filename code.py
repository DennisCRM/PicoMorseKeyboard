# Morse Code specs:
# dit - 1 element
# dah - 3 elements
# space between letters- 3 elements
# space between words  - 7 elements
# PARIS - 50 elements (standard word)
# elements per s = (50 x wpm) / 60
# sec / element = 60 / (50 * wpm)
# wpm = 60 / (50 * secPerElement)
#         = 1.2 / secPerElements
#         = 1200 / msPerElement
# example:
# for 20wpm, 60 milliseconds dit
# 25 wpm - 48 ms
# Display:  0123456789......
#          +----------------+
#          |10.00 <<.>><<->>|
#          |ped cw text here|
#          +----------------+

import supervisor
import time
import board
import busio
from digitalio import DigitalInOut, Direction, Pull
import usb_hid
from adafruit_hid.keyboard import Keyboard
from adafruit_hid.keycode import Keycode
# import adafruit_character_lcd.character_lcd_i2c as character_lcd
from lcd.lcd import LCD
from lcd.i2c_pcf8574_interface import I2CPCF8574Interface
# from lcd.lcd import CursorMode

# code for dealing with time in milliseconds
_TICKS_PERIOD = const(1 << 29)
_TICKS_MAX = const(_TICKS_PERIOD-1)
_TICKS_HALFPERIOD = const(_TICKS_PERIOD//2)
def ticks_add(ticks, delta):
    return (ticks + delta) % _TICKS_PERIOD
def ticks_diff(ticks1, ticks2):
    diff = (ticks1 - ticks2) & _TICKS_MAX
    diff = ((diff + _TICKS_HALFPERIOD) & _TICKS_MAX) - _TICKS_HALFPERIOD
    return diff

# starting time = 5WPM
ditTime = 240  # 60 / (50 * wpm) = 0.240 sec
ditPrecision = 1.6  # buffer factor for ditTime, before keydown time is considered a DAH
accuracyGoal1 = 0.1  # percentage away from ditTime that's not considered "spot on"
accuracyGoal2 = 0.3  # 2nd level of percentage away from ditTime

# below are all calculated with adjustWPM()
letterBreakTime = 720  # 3 x ditTime
wordBreakTime = 2400  # 7 x ditTime (+ letterBreakTime)
ditLow1 = 0
ditLow2 = 0
ditHigh1 = 0
ditHigh2 = 0
dahLow1 = 0
dahLow2 = 0
dahHigh1 = 0
dahHigh2 = 0

# create lists of dit/dah times for calculating WPM, and intialize them
historySize = 10  # determines how long before WPM changes
pastDitTimes = []
pastDahTimes = []
for n in range(historySize):
    pastDitTimes.append(ditTime)
    pastDahTimes.append(ditTime*3)

buffer = ""  # buffer of dits and dahs
charsTyped = "                "  # history of characters typed for lcd

waitingForLetterBreak = False
waitingForWordBreak = False
keyUpTimestamp = 0
keyDownTimestamp = 0

morseToKeyCodes = {
    ".-": ["a", [Keycode.A]],
    "-...": ["b", [Keycode.B]],
    "-.-.": ["c", [Keycode.C]],
    "-..": ["d", [Keycode.D]],
    ".": ["e", [Keycode.E]],
    "..-.": ["f", [Keycode.F]],
    "--.": ["g", [Keycode.G]],
    "....": ["h", [Keycode.H]],
    "..": ["i", [Keycode.I]],
    ".---": ["j", [Keycode.J]],
    "-.-": ["k", [Keycode.K]],
    ".-..": ["l", [Keycode.L]],
    "--": ["m", [Keycode.M]],
    "-.": ["n", [Keycode.N]],
    "---": ["o", [Keycode.O]],
    ".--.": ["p", [Keycode.P]],
    "--.-": ["q", [Keycode.Q]],
    ".-.": ["r", [Keycode.R]],
    "...": ["s", [Keycode.S]],
    "-": ["t", [Keycode.T]],
    "..-": ["u", [Keycode.U]],
    "...-": ["v", [Keycode.V]],
    ".--": ["w", [Keycode.W]],
    "-..-": ["x", [Keycode.X]],
    "-.--": ["y", [Keycode.Y]],
    "--..": ["z", [Keycode.Z]],
    ".----": ["1", [Keycode.ONE]],
    "..---": ["2", [Keycode.TWO]],
    "...--": ["3", [Keycode.THREE]],
    "....-": ["4", [Keycode.FOUR]],
    ".....": ["5", [Keycode.FIVE]],
    "-....": ["6", [Keycode.SIX]],
    "--...": ["7", [Keycode.SEVEN]],
    "---..": ["8", [Keycode.EIGHT]],
    "----.": ["9", [Keycode.NINE]],
    "-----": ["0", [Keycode.ZERO]],
    "-...-": ["=", [Keycode.EQUALS]],
    "--..--": [",", [Keycode.COMMA]],
    ".-.-.-": [".", [Keycode.PERIOD]],
    "-..-.": ["/", [Keycode.FORWARD_SLASH]],
    ".----.": ["'", [Keycode.QUOTE]],
    "-....-": ["-", [Keycode.MINUS]],
}

morseToShiftedKeycodes = {
    ".-...": ["AS", [Keycode.A, Keycode.S]],  # also means ampersand %
    # ".-...": ["&", Keycode.SEVEN]],
    "-...-.-": ["BK", [Keycode.B, Keycode.K]],
    "-.-.-": ["CT", [Keycode.C, Keycode.T]],
    ".-.-.": ["EC", [Keycode.E, Keycode.C]],
    "........": ["HH", [Keycode.H, Keycode.H]],
    "-.--.": ["KN", [Keycode.K, Keycode.N]],
    ".-.-": ["RT", [Keycode.R, Keycode.T]],
    "...---...": ["SOS", [Keycode.S, Keycode.O, Keycode.S]],
    "...-.-": ["VA", [Keycode.V, Keycode.A]],
    "...-.": ["VE", [Keycode.V, Keycode.E]],
    "..--..": ["?", [Keycode.FORWARD_SLASH]],
}

# set up keyer
keyerPin = board.GP2
keyerSwitch = DigitalInOut(keyerPin)
keyerSwitch.direction = Direction.INPUT
keyerSwitch.pull = Pull.UP

# set up LED
led = DigitalInOut(board.LED)
led.direction = Direction.OUTPUT

# initialize I2C
i2c = busio.I2C(scl=board.GP5, sda=board.GP4, frequency=786_000)

# initialize LCD
address = 0x27
lcd = LCD(I2CPCF8574Interface(i2c, address), num_rows=2, num_cols=16)
# lcd = character_lcd.Character_LCD_I2C(i2c, cols, rows)

time.sleep(0.5) # wait for USB connection
#initialize HID keyboard interface
kbd = False
if supervisor.runtime.usb_connected:
    kbd = Keyboard(usb_hid.devices) 
    
def pushRight(value, list1=[]):
    lastItem = len(list1) - 1
    for n in range(lastItem):
        list1[n] = list1[n+1]
    list1[lastItem] = value
    return list1

def keyUp():
    return keyerSwitch.value

def keyDown():
    return not keyerSwitch.value

def wpm():
    global ditTime
    wordsPerMinute = 1200.0 / ditTime
    return "{:>5.2f}".format(wordsPerMinute)

def typeKeys(keyCodes):
    for code in keyCodes:
        kbd.press(code)
        kbd.release(code)

def isDit(timeDown):
    if(timeDown < ditTime * ditPrecision):
        return True
    else:
        return False

def adjustWPM():
    global ditTime
    global letterBreakTime
    global wordBreakTime
    global ditLow1
    global ditLow2
    global ditHigh1
    global ditHigh2
    global dahLow1
    global dahLow2
    global dahHigh1
    global dahHigh2
    avgDit = sum(pastDitTimes) / len(pastDitTimes)
    avgDah = sum(pastDahTimes) / len(pastDitTimes)
    # average dit and dah time to get new unit time
    unitDah = avgDah / 3
    ditTime = (avgDit + unitDah) / 2
    letterBreakTime = 3 * ditTime
    wordBreakTime = 7 * ditTime
    #calculate goal times
    ditLow1 = ditTime * (1 - accuracyGoal1)
    ditHigh1 = ditTime * (1 + accuracyGoal1)
    ditLow2 = ditTime * (1 - accuracyGoal2)
    ditHigh2 = ditTime * (1 + accuracyGoal2)
    dahLow1 = 3 * ditLow1
    dahHigh1 = 3 * ditHigh1
    dahLow2 = 3 * ditLow2
    dahHigh2 = 3* ditHigh2

def processBuffer():
    global buffer
    global charsTyped
    if buffer in morseToKeyCodes:
        if kbd:
            typeKeys(morseToKeyCodes[buffer][1])
        charsTyped += morseToKeyCodes[buffer][0]
    elif buffer in morseToShiftedKeycodes:
        if kbd:
            kbd.press(Keycode.RIGHT_SHIFT)
            typeKeys(morseToShiftedKeycodes[buffer][1])
            kbd.release(Keycode.RIGHT_SHIFT)
        charsTyped += morseToShiftedKeycodes[buffer][0]
    charsTyped = charsTyped[-16:]
    lcd.set_cursor_pos(1, 0)
    lcd.print(charsTyped)
    buffer = ""

def printWPM():
    lcd.set_cursor_pos(0, 0)
    lcd.print(wpm())

def printAccuracyDit(timeDown):
    lcd.set_cursor_pos(0, 6)
    if timeDown < ditLow2:
        lcd.print("<<.  ")
    elif timeDown < ditLow1:
        lcd.print(" <.  ")
    elif timeDown > ditHigh1:
        lcd.print("  .> ")
    elif timeDown > ditHigh2:
        lcd.print("  .>>")
    else:
        lcd.print("  .  ")

def printAccuracyDah(timeDown):
    lcd.set_cursor_pos(0, 11)
    if timeDown < dahLow2:
        lcd.print("<<-  ")
    elif timeDown < dahLow1:
        lcd.print(" <-  ")
    elif timeDown > dahHigh1:
        lcd.print("  -> ")
    elif timeDown > dahHigh2:
        lcd.print("  ->>")
    else:
        lcd.print("  -  ")

# do something to show we're alive
# for x in range(6):
#     led.value = True
#     time.sleep(0.1)
#     led.value = False
#     time.sleep(0.1)

adjustWPM()
printWPM()
while True:
    while keyUp():
        if (waitingForLetterBreak and
                ticks_diff(supervisor.ticks_ms(), keyUpTimestamp) > letterBreakTime):
            processBuffer()
            printWPM()
            waitingForLetterBreak = False
            waitingForWordBreak = True
        if (waitingForWordBreak and
                ticks_diff(supervisor.ticks_ms(), keyUpTimestamp) > wordBreakTime):
            charsTyped += " "
            charsTyped = charsTyped[-16:]
            lcd.set_cursor_pos(1, 0)
            lcd.print(charsTyped)
            if kbd:
                typeKeys([Keycode.SPACEBAR])
            waitingForWordBreak = False
    keyDownTimestamp = supervisor.ticks_ms()
    led.value = True
    time.sleep(0.02)  # debounce (note: this means minimum 20ms timing for dit)
    while keyDown():
        pass
    keyUpTimestamp = supervisor.ticks_ms()
    led.value = False
    timeDown = ticks_diff(keyUpTimestamp, keyDownTimestamp)  # time key was down
    # print("ticks: {:n}".format(timeDown))
    if isDit(timeDown):
        buffer += "."
        pastDitTimes = pushRight(timeDown, pastDitTimes)
        printAccuracyDit(timeDown)
    else:
        buffer += "-"
        pastDahTimes = pushRight(timeDown, pastDahTimes)
        printAccuracyDah(timeDown)
    adjustWPM()
    waitingForLetterBreak = True
    # time.sleep(0.02)  # don't need to debounce since above code takes about 20ms


