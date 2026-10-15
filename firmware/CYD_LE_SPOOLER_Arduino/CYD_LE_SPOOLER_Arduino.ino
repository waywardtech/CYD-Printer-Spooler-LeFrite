// ============================================================================
//  LE SPOOLER - Cheap Yellow Display firmware (v0.2, NOT YET FLASHED)
//
//  A retro fanfold daisywheel "printer" on an ESP32-2432S028R.
//
//    Linux HTTP spooler  --Wi-Fi-->  this sketch  -->  TFT paper + SD card
//                        --USB serial (optional) -->
//
//  What this sketch does (compiles in CI; untested on real hardware):
//    * splash screen, Wi-Fi connect, GET /health, poll GET /next, POST /ack/<id>
//    * portrait fanfold paper: greenbar bands, sprocket holes, perforations
//    * character-by-character printing with BS / CR / LF / FF / BEL / TAB
//    * real on-screen overstrike: glyphs are drawn with a transparent background,
//      so  e \b '  literally stamps an apostrophe over the e -> é
//    * ribbon wear (ink fades), optional chaos events (off by default)
//    * DAC "mechanism" sounds on GPIO26
//    * save jobs to SD, a very simple touch browser, ASCII animation player
//    * @LE_SPOOLER envelopes over USB serial, answered with @ACK <id>
//
//  Before flashing:
//    1. copy config.example.h -> config.h and fill it in
//    2. build with ../build.sh (passes the TFT_eSPI pin config as flags)
//    3. verify every pin marked UNVERIFIED in config.h against your board
//
//  Libraries (Arduino Library Manager):
//    TFT_eSPI (Bodmer), ArduinoJson (v7, Benoit Blanchon)
//    XPT2046_Touchscreen (Paul Stoffregen), only if TOUCH_USE_LIBRARY = 1
//  Built into the esp32 core: WiFi, HTTPClient, SPI, SD, FS
// ============================================================================

#include <Arduino.h>
#include <WiFi.h>
#include <HTTPClient.h>
#include <SPI.h>
#include <FS.h>
#include <SD.h>
#include <TFT_eSPI.h>
#include <ArduinoJson.h>

#if __has_include("config.h")
  #include "config.h"
#else
  #warning "config.h not found - using config.example.h. Copy it to config.h and edit!"
  #include "config.example.h"
#endif

#if TOUCH_USE_LIBRARY
  #include <XPT2046_Touchscreen.h>
#endif

// ----------------------------------------------------------------------------
//  Screen & paper geometry (portrait 240 x 320). See notes/hardware-notes.md
// ----------------------------------------------------------------------------
static const int SCREEN_W     = 240;
static const int SCREEN_H     = 320;
static const int STATUS_H     = 10;                 // status bar at the very top
static const int STRIP_W      = 13;                 // tractor-feed strip, each side
static const int PERF_L_X     = STRIP_W;            // left vertical perforation
static const int PERF_R_X     = SCREEN_W - STRIP_W - 1;
static const int CHAR_W       = 6;                  // GLCD font 1: 5x7 in a 6x8 cell
static const int LINE_H       = 10;                 // 8 px glyph + 2 px leading
static const int TEXT_X0      = PERF_L_X + 4;       // left margin inside the perf
static const int COLS         = (PERF_R_X - 3 - TEXT_X0) / CHAR_W;   // = 34
static const int PAPER_Y0     = STATUS_H;
static const int PLATEN_H     = 2 * LINE_H;         // dark platen/bail bar at bottom
static const int ROWS         = (SCREEN_H - PAPER_Y0 - PLATEN_H) / LINE_H;  // visible paper rows
static const int HEAD_ROW     = ROWS - 1;           // the print head sits on the lowest visible row
static const int BAND_LINES   = 3;                  // greenbar band = 1/2" = 3 lines

// Colours (RGB565), set in setup() once the TFT exists.
static uint16_t C_GREEN, C_CREAM, C_STRIP, C_HOLE, C_PERF, C_PLATEN, C_STATUS_BG, C_STATUS_FG;

// ----------------------------------------------------------------------------
//  Printer state
// ----------------------------------------------------------------------------
struct Cell {
  char    a;      // first glyph struck in this cell (0 = blank)
  char    b;      // second glyph (overstrike), 0 if none
  uint8_t ink;    // ribbon % when struck (for fading)
  int8_t  toneA;  // per-strike ink variation of each glyph
  int8_t  toneB;
};

// Ring buffer of the lines currently visible on screen. Index with rowSlot().
static Cell  lines[ROWS][COLS];
static long  paperLine   = 0;      // absolute line number under the head
static int   headCol     = 0;
static float ribbonPct   = RIBBON_START_PCT;
static int   chaosLevel  = CHAOS_LEVEL;
static bool  sdOk        = false;
static bool  wifiOk      = false;
static unsigned long lastPoll = 0;

TFT_eSPI tft = TFT_eSPI();
SPIClass sdSPI(VSPI);
#if TOUCH_USE_LIBRARY
SPIClass touchSPI(VSPI);   // NOTE: shares VSPI with SD. Only use one at a time.
XPT2046_Touchscreen ts(TOUCH_CS, TOUCH_IRQ);
#endif

// The public API surface of the printer
void drawPaper();
void drawSprocketHoles(int row, long line);
void drawPerforations(int row, long line);
void printCharacter(char c);
void printTextSlowly(const String &text);
void playClick();
void playCarriageReturn();
void playLineFeed();
void playFormFeed();
void playBell();
void playJam();
bool saveJobToSD(const String &id, const String &title, const String &body);
void browseDocuments();
void playAsciiAnimation(const String &animText);

// ============================================================================
//  Small helpers
// ============================================================================
static int  rowSlot(long line)   { long m = line % ROWS; return (int)(m < 0 ? m + ROWS : m); }
static int  formLine(long line)  { long m = line % FORM_LINES; return (int)(m < 0 ? m + FORM_LINES : m); }
static int  rowY(int row)        { return PAPER_Y0 + row * LINE_H; }
static long lineAtRow(int row)   { return paperLine - (HEAD_ROW - row); }
static int  colX(int col)        { return TEXT_X0 + col * CHAR_W; }

// Blue-black ribbon ink. 100% -> near black, 0% -> pale grey (old ribbons fade,
// they don't vanish). `tone` is the per-strike hammer-pressure variation.
static uint16_t inkColor(uint8_t ink, int tone = 0) {
  int v = constrain(22 + tone + (100 - ink) * 165 / 100, 0, 215);
  return tft.color565(v, v, min(255, v + 12));
}

static uint16_t paperColor(long line) {
  return ((formLine(line) / BAND_LINES) % 2) == 0 ? C_GREEN : C_CREAM;
}

// One hammer strike of type onto paper: transparent background (so overtype
// really layers ink), uneven ink, and a couple of tiny dropouts where the
// typeface didn't quite bite. Dropouts come from a hash of the cell, so a
// glyph looks the same every time the paper is redrawn.
static void stampGlyph(int x, int y, char c, uint8_t ink, int tone, uint16_t paper, uint32_t seed) {
  uint16_t col = inkColor(ink, tone);
  tft.drawChar(x, y, c, col, col, 1);                // fg == bg -> transparent background
  uint32_t h = seed * 2654435761u;
  int drops = (h >> 29) % 3;                         // 0..2 dropouts
  for (int i = 0; i < drops; i++) {
    h = h * 1103515245u + 12345u;
    tft.drawPixel(x + (h >> 16) % 5, y + (h >> 8) % 7, paper);
  }
}

static void setLed(bool r, bool g, bool b) {    // active LOW
  digitalWrite(LED_R, r ? LOW : HIGH);
  digitalWrite(LED_G, g ? LOW : HIGH);
  digitalWrite(LED_B, b ? LOW : HIGH);
}

static void drawStatus(const String &msg) {
  tft.fillRect(0, 0, SCREEN_W, STATUS_H, C_STATUS_BG);
  tft.setTextColor(C_STATUS_FG, C_STATUS_BG);
  tft.setTextFont(1);
  tft.setTextSize(1);
  tft.setCursor(2, 1);
  tft.print("LE SPOOLER ");
  tft.print(msg.substring(0, 22));
  tft.setCursor(SCREEN_W - 6 * 7, 1);
  tft.printf("RIB%3d%%", (int)ribbonPct);
}

// ============================================================================
//  Paper drawing
// ============================================================================

// Tractor-feed sprocket holes: one per greenbar band (every 1/2", every 3 lines).
void drawSprocketHoles(int row, long line) {
  int y = rowY(row);
  tft.fillRect(0, y, STRIP_W, LINE_H, C_STRIP);
  tft.fillRect(PERF_R_X + 1, y, SCREEN_W - PERF_R_X - 1, LINE_H, C_STRIP);
  if (formLine(line) % BAND_LINES == 1) {
    tft.fillCircle(STRIP_W / 2, y + LINE_H / 2, 3, C_HOLE);
    tft.fillCircle(SCREEN_W - 1 - STRIP_W / 2, y + LINE_H / 2, 3, C_HOLE);
  }
}

// Vertical perforations between the strips and the page; horizontal perforation
// (the fold) at the top of each form.
void drawPerforations(int row, long line) {
  int y = rowY(row);
  for (int dy = 0; dy < LINE_H; dy += 3) {
    tft.drawPixel(PERF_L_X, y + dy, C_PERF);
    tft.drawPixel(PERF_R_X, y + dy, C_PERF);
  }
  if (formLine(line) == 0) {
    for (int x = 0; x < SCREEN_W; x += 4) tft.drawFastHLine(x, y, 2, C_PERF);
  }
}

// Draw one paper row: band background, strips, perforations, then its text.
static void drawRow(int row) {
  long line  = lineAtRow(row);
  int  y     = rowY(row);
  uint16_t paper = paperColor(line);
  tft.fillRect(STRIP_W + 1, y, PERF_R_X - STRIP_W - 1, LINE_H, paper);
  drawSprocketHoles(row, line);
  drawPerforations(row, line);
  Cell *cells = lines[rowSlot(line)];
  for (int c = 0; c < COLS; c++) {
    uint32_t seed = (uint32_t)line * 977u + c * 131u;
    if (cells[c].a) stampGlyph(colX(c), y + 1, cells[c].a, cells[c].ink, cells[c].toneA, paper, seed);
    if (cells[c].b) stampGlyph(colX(c), y + 1, cells[c].b, cells[c].ink, cells[c].toneB, paper, seed + 7);
  }
}

static void drawPlaten() {
  int y = rowY(ROWS);
  tft.fillRect(0, y, SCREEN_W, SCREEN_H - y, C_PLATEN);
  tft.fillRect(colX(min(headCol, COLS - 1)), y + 2, CHAR_W - 1, 4, TFT_ORANGE);  // carriage
}

// Full redraw of the visible paper (used after every line feed: paper scrolls up).
// TODO(v1): use TFT hardware scrolling or a TFT_eSprite to avoid the full redraw.
void drawPaper() {
  for (int r = 0; r < ROWS; r++) drawRow(r);
  drawPlaten();
}

// ============================================================================
//  Sounds (DAC on SPEAKER_PIN). Crude but charming. All blocking and short.
// ============================================================================
static void noiseBurst(int durationUs, int amp, int decay) {
#if SOUND_ENABLED
  unsigned long t0 = micros();
  while ((long)(micros() - t0) < durationUs) {
    dacWrite(SPEAKER_PIN, random(0, max(1, amp)));
    delayMicroseconds(30);
    if (decay && amp > 0) amp -= decay;
  }
  dacWrite(SPEAKER_PIN, 0);
#else
  (void)durationUs; (void)amp; (void)decay;
#endif
}

void playClick()          { noiseBurst(1800, SOUND_VOLUME, 1); }            // type hammer
void playCarriageReturn() { noiseBurst(60000, SOUND_VOLUME / 3, 0);          // carriage whirr...
                            noiseBurst(4000, SOUND_VOLUME, 2); }             // ...thunk
void playLineFeed()       { noiseBurst(2500, SOUND_VOLUME / 2, 1); delay(15);
                            noiseBurst(2500, SOUND_VOLUME / 2, 1); }         // ratchet-ratchet
void playFormFeed()       { for (int i = 0; i < 12; i++) { playLineFeed(); delay(10); } }
void playJam()            { noiseBurst(400000, SOUND_VOLUME / 2, 0); }       // grinding

void playBell() {
#if SOUND_ENABLED
  // ~2 kHz square wave with a decaying amplitude: "ding"
  for (int amp = SOUND_VOLUME; amp > 0; amp -= 1) {
    for (int k = 0; k < 6; k++) {
      dacWrite(SPEAKER_PIN, amp); delayMicroseconds(250);
      dacWrite(SPEAKER_PIN, 0);   delayMicroseconds(250);
    }
  }
#endif
}

// ============================================================================
//  The print mechanism
// ============================================================================
static void clearLine(long line) {
  memset(lines[rowSlot(line)], 0, sizeof(Cell) * COLS);
}

static void lineFeed() {
  paperLine++;
  clearLine(paperLine);          // the slot scrolling into view is reused for the new line
  playLineFeed();
  drawPaper();
}

static void carriageReturn() {
  headCol = 0;
  playCarriageReturn();
  drawPlaten();
}

static void formFeed() {
  if (headCol > 0) { headCol = 0; lineFeed(); }
  while (formLine(paperLine) != 0) {
    paperLine++;
    clearLine(paperLine);
  }
  playFormFeed();
  drawPaper();
}

// Chaos: rare, gentle, off by default. Probabilities are per character.
static void maybeChaos() {
  if (chaosLevel <= 0) return;
  static const long jamOdds[] = {0, 6000, 1500, 300};
  static const long outOdds[] = {0, 12000, 4000, 900};
  if (random(jamOdds[chaosLevel]) == 0) {
    setLed(true, false, false);
    drawStatus("** PAPER JAM **");
    playJam();
    delay(1200);
    drawStatus("jam cleared");
    setLed(false, false, false);
  }
  if (random(outOdds[chaosLevel]) == 0) {
    setLed(true, true, false);
    drawStatus("** PAPER OUT **");
    playBell(); delay(200); playBell();
    delay(1500);
    formFeed();                                     // "new stack loaded"
    drawStatus("paper loaded");
    setLed(false, false, false);
  }
}

// Strike (or handle) one character. This is the heart of the printer.
void printCharacter(char c) {
  switch (c) {
    case '\n': headCol = 0; lineFeed(); return;     // LF treated as CR+LF (see protocol.md)
    case '\r': carriageReturn(); return;
    case '\b': if (headCol > 0) headCol--; drawPlaten(); return;
    case '\f': formFeed(); return;
    case '\a': playBell(); return;
    case '\t': headCol = min(COLS - 1, (headCol / 8 + 1) * 8); drawPlaten(); return;
    default: break;
  }
  if ((uint8_t)c < 0x20 || c == 0x7F) return;       // other control chars: ignore
  if (headCol >= COLS) { headCol = 0; lineFeed(); } // right margin: wrap

  maybeChaos();

  Cell &cell = lines[rowSlot(paperLine)][headCol];
  if (c != ' ') {
    int tone = random(-12, 22);                     // uneven hammer pressure
    bool second = cell.a != 0;
    if (!second) { cell.a = c; cell.ink = (uint8_t)ribbonPct; cell.toneA = tone; }
    else         { cell.b = c; cell.toneB = tone; } // overstrike: both glyphs stay inked

    // Misstrike (chaos): nudge the glyph by one pixel, as a skewed hammer would
    int nudge = (chaosLevel > 0 && random(600 / chaosLevel) == 0) ? 1 : 0;

    // Draw ONLY the new glyph, transparently, on top of whatever is there.
    // That is exactly what a daisywheel does, so accents come out naturally.
    int y = rowY(HEAD_ROW) + 1 + nudge;
    if (second && strchr("'`^~\"", c)) y -= 1;      // lift accents 1 px over letters
    uint32_t seed = (uint32_t)paperLine * 977u + headCol * 131u + (second ? 7 : 0);
    stampGlyph(colX(headCol), y, c, (uint8_t)ribbonPct, tone, paperColor(paperLine), seed);

    playClick();
    if (chaosLevel > 0) ribbonPct = max(0.0f, ribbonPct - 0.004f * chaosLevel);
  }
  headCol++;
  drawPlaten();
}

// Type a whole document at CHARS_PER_SECOND with a little human jitter.
void printTextSlowly(const String &text) {
  const int baseDelay = 1000 / max(1, CHARS_PER_SECOND);
  setLed(false, true, false);
  for (size_t i = 0; i < text.length(); i++) {
    char c = text[i];
    printCharacter(c);
    if ((uint8_t)c >= 0x20) delay(baseDelay * random(75, 126) / 100);
    if (USB_SERIAL_JOBS && Serial.available() && Serial.peek() == 0x18) { Serial.read(); break; }  // Ctrl-X cancels
  }
  // Feed a couple of lines so the last line clears the head (tear-off position).
  headCol = 0; lineFeed(); lineFeed();
  setLed(false, false, false);
  drawStatus("READY");
}

// ============================================================================
//  microSD: save / browse / replay
// ============================================================================
bool saveJobToSD(const String &id, const String &title, const String &body) {
  if (!sdOk) return false;
  if (!SD.exists(SD_SPOOL_DIR)) SD.mkdir(SD_SPOOL_DIR);
  String path = String(SD_SPOOL_DIR) + "/" + id + ".txt";
  File f = SD.open(path, FILE_WRITE);
  if (!f) return false;
  // Same @LE_SPOOLER envelope as docs/protocol.md, so files round-trip.
  f.print("@LE_SPOOLER_BEGIN\n");
  f.print("ID: ");    f.print(id);    f.print("\n");
  f.print("TITLE: "); f.print(title); f.print("\n");
  f.print("MODE: PRINT\n\n");
  f.print(body);
  if (!body.endsWith("\n")) f.print("\n");
  f.print("@LE_SPOOLER_END\n");
  f.close();
  return true;
}

// Body of an envelope: everything after the first blank line, up to @LE_SPOOLER_END.
static String envelopeBody(const String &text) {
  int start = text.indexOf("\n\n");
  int end   = text.lastIndexOf("@LE_SPOOLER_END");
  if (!text.startsWith("@LE_SPOOLER_BEGIN") || start < 0) return text;
  return text.substring(start + 2, end > start ? end : (int)text.length());
}

static String envelopeHeader(const String &text, const String &key) {
  String needle = String("\n") + key + ": ";
  int p = text.indexOf(needle);
  if (p < 0) return "";
  int e = text.indexOf('\n', p + 1);
  return text.substring(p + needle.length(), e);
}

// Touch (bit-banged XPT2046 by default - see config.h)
static uint16_t xptRead(uint8_t cmd) {
#if !TOUCH_USE_LIBRARY
  digitalWrite(TOUCH_CS, LOW);
  for (int i = 7; i >= 0; i--) {                     // send command, MSB first
    digitalWrite(TOUCH_MOSI, (cmd >> i) & 1);
    digitalWrite(TOUCH_CLK, HIGH); digitalWrite(TOUCH_CLK, LOW);
  }
  uint16_t v = 0;
  for (int i = 0; i < 16; i++) {                     // 16 clocks, 12-bit result
    digitalWrite(TOUCH_CLK, HIGH); digitalWrite(TOUCH_CLK, LOW);
    v = (v << 1) | digitalRead(TOUCH_MISO);
  }
  digitalWrite(TOUCH_CS, HIGH);
  return v >> 4;
#else
  (void)cmd;
  return 0;
#endif
}

static bool readTouch(int &x, int &y) {
#if TOUCH_USE_LIBRARY
  if (!ts.touched()) return false;
  TS_Point p = ts.getPoint();
  int rx = p.x, ry = p.y;
#else
  if (digitalRead(TOUCH_IRQ) == HIGH) return false;  // IRQ is LOW while pressed
  int rx = xptRead(0xD0), ry = xptRead(0x90);
#endif
  x = map(rx, TOUCH_X_MIN, TOUCH_X_MAX, 0, SCREEN_W);
  y = map(ry, TOUCH_Y_MIN, TOUCH_Y_MAX, 0, SCREEN_H);
  return true;
}

// Minimal document browser: list up to 25 saved jobs, tap one to replay it.
void browseDocuments() {
  if (!sdOk) { drawStatus("no SD card"); delay(1000); return; }
  String names[25];
  int n = 0;
  File dir = SD.open(SD_SPOOL_DIR);
  if (dir) {
    for (File f = dir.openNextFile(); f && n < 25; f = dir.openNextFile()) {
      if (!f.isDirectory()) names[n++] = String(f.name());
      f.close();
    }
    dir.close();
  }
  tft.fillScreen(C_CREAM);
  tft.setTextColor(TFT_BLACK, C_CREAM);
  tft.setCursor(4, 4); tft.print("SAVED DOCUMENTS (tap; top = back)");
  for (int i = 0; i < n; i++) { tft.setCursor(4, 20 + i * 12); tft.print(names[i]); }
  if (n == 0) { tft.setCursor(4, 20); tft.print("(nothing yet)"); }

  unsigned long t0 = millis();
  int x, y;
  while (millis() - t0 < 30000) {                    // 30 s timeout back to paper
    if (readTouch(x, y)) {
      delay(150);
      if (y < 16) break;
      int i = (y - 20) / 12;
      if (i >= 0 && i < n) {
        File f = SD.open(String(SD_SPOOL_DIR) + "/" + names[i]);
        String text = f ? f.readString() : String();
        if (f) f.close();
        drawPaper(); drawStatus("REPLAY");
        printTextSlowly(envelopeBody(text));
        return;
      }
    }
    delay(20);
  }
  drawPaper(); drawStatus("READY");
}

// ============================================================================
//  ASCII animation player (fixed screen, separate from paper mode)
//  animText is a whole .anim file (docs/protocol.md section 3).
// ============================================================================
//
//  Touch controls:
//    while playing:  tap anywhere            -> pause
//    while paused:   left third  (◀)         -> previous frame
//                    middle      (▶/❚❚)      -> resume
//                    right third (▶)         -> next frame
//                    top bar, left  (EXIT)  -> back to the paper
//                    top bar, right (PRINT) -> print this frame onto the paper
//  It plays ANIM_LOOPS times untouched, then returns to the paper by itself.
// ============================================================================
static const int MAX_FRAMES = 200;
static const int ANIM_LOOPS = 3;
static const int ANIM_X0 = (SCREEN_W - 32 * CHAR_W) / 2, ANIM_Y0 = 60;
static const int BAR_H = 36;                         // touch bars top and bottom

static void waitForRelease() {
  int x, y;
  while (readTouch(x, y)) delay(15);
  delay(40);
}

static void drawAnimChrome(int i, int n, bool paused) {
  tft.fillRect(0, 0, SCREEN_W, BAR_H, TFT_DARKGREY);
  tft.setTextColor(TFT_WHITE, TFT_DARKGREY);
  tft.setCursor(6, 14);  tft.print("< EXIT");
  tft.setCursor(SCREEN_W / 2 - 30, 14); tft.printf("%3d/%-3d", i + 1, n);
  tft.setCursor(SCREEN_W - 48, 14); tft.print(paused ? "PRINT" : "");
  int y = SCREEN_H - BAR_H;
  tft.fillRect(0, y, SCREEN_W, BAR_H, paused ? TFT_DARKGREEN : TFT_BLACK);
  tft.setTextColor(TFT_WHITE, paused ? TFT_DARKGREEN : TFT_BLACK);
  tft.setCursor(6, y + 14);
  tft.print(paused ? "  < prev      PLAY      next >" : "         tap to pause");
}

static void drawAnimFrame(const String &frame) {
  tft.fillRect(0, ANIM_Y0, SCREEN_W, 13 * LINE_H, TFT_BLACK);
  tft.setTextColor(TFT_GREEN, TFT_BLACK);            // phosphor green, because of course
  int row = 0, pos = 0;
  while (pos <= (int)frame.length() && row < 13) {
    int eol = frame.indexOf('\n', pos);
    if (eol < 0) eol = frame.length();
    tft.setCursor(ANIM_X0, ANIM_Y0 + row * LINE_H);
    tft.print(frame.substring(pos, eol));
    pos = eol + 1;
    row++;
  }
}

void playAsciiAnimation(const String &animText) {
  float fps = 4;
  int fpsPos = animText.indexOf("# fps:");
  if (fpsPos >= 0) fps = constrain(animText.substring(fpsPos + 6).toFloat(), 0.1f, 60.0f);
  const unsigned long frameMs = (unsigned long)(1000 / fps);
  const bool loops = animText.indexOf("# loop: no") < 0;

  // Index the frames: each starts after a line that is exactly "---".
  static int starts[MAX_FRAMES], ends[MAX_FRAMES];
  int n = 0, pos = 0;
  while (n < MAX_FRAMES) {
    int sep = animText.indexOf("\n---\n", pos);
    if (sep < 0) break;
    if (n > 0) ends[n - 1] = sep;
    starts[n++] = sep + 5;
    pos = sep + 4;                                   // keep the '\n' so back-to-back separators match
  }
  if (n == 0) return;
  ends[n - 1] = animText.length();
  auto frameText = [&](int i) { return animText.substring(starts[i], max(starts[i], ends[i])); };

  tft.fillScreen(TFT_BLACK);
  int i = 0, played = 0;
  bool paused = false;
  drawAnimChrome(i, n, paused);
  drawAnimFrame(frameText(i));
  unsigned long last = millis();

  while (true) {
    int tx, ty;
    if (readTouch(tx, ty)) {
      waitForRelease();
      if (!paused) {
        paused = true;
      } else if (ty < BAR_H && tx < SCREEN_W / 3) {
        break;                                       // EXIT
      } else if (ty < BAR_H && tx > SCREEN_W * 2 / 3) {
        String f = frameText(i);                     // PRINT this frame onto the paper
        drawPaper(); drawStatus("frame " + String(i + 1));
        printTextSlowly(f + "\n");
        return;
      } else if (ty < BAR_H) {
        // middle of the top bar: frame counter, ignore
      } else if (tx < SCREEN_W / 3) {
        i = (i - 1 + n) % n;
      } else if (tx > SCREEN_W * 2 / 3) {
        i = (i + 1) % n;
      } else {
        paused = false;
      }
      drawAnimChrome(i, n, paused);
      drawAnimFrame(frameText(i));
      last = millis();
    }
    if (!paused && millis() - last >= frameMs) {
      last = millis();
      if (i + 1 >= n) {
        if (!loops || ++played >= ANIM_LOOPS) break;
      }
      i = (i + 1) % n;
      drawAnimFrame(frameText(i));
      drawAnimChrome(i, n, paused);
    }
    if (USB_SERIAL_JOBS && Serial.available()) break; // a print job arrived: get out of the way
    delay(10);
  }
  drawPaper(); drawStatus("READY");
}

static void playAnimationFromServer(const String &name) {
  if (!wifiOk) return;
  HTTPClient http;
  http.begin(String(LA_FRITE_SERVER_URL) + "/animations/" + name + "?format=raw");
  http.setTimeout(HTTP_TIMEOUT_MS);
  if (http.GET() == 200) playAsciiAnimation(http.getString());
  http.end();
}

// ============================================================================
//  Network: Wi-Fi, /health, /next, /ack
// ============================================================================
static void connectWiFi() {
  drawStatus("Wi-Fi...");
  WiFi.mode(WIFI_STA);
  WiFi.begin(WIFI_SSID, WIFI_PASSWORD);
  unsigned long t0 = millis();
  while (WiFi.status() != WL_CONNECTED && millis() - t0 < 15000) delay(250);
  wifiOk = WiFi.status() == WL_CONNECTED;
  drawStatus(wifiOk ? WiFi.localIP().toString() : String("no Wi-Fi: serial only"));
}

static bool checkHealth() {
  HTTPClient http;
  http.begin(String(LA_FRITE_SERVER_URL) + "/health");
  http.setTimeout(HTTP_TIMEOUT_MS);
  int code = http.GET();
  http.end();
  return code == 200;
}

static void ackJob(const String &id) {
  HTTPClient http;
  http.begin(String(LA_FRITE_SERVER_URL) + "/ack/" + id);
  http.setTimeout(HTTP_TIMEOUT_MS);
  http.POST("");
  http.end();
}

// Ask the spooler for the next job. 204 = nothing to do.
static void pollNext() {
  HTTPClient http;
  http.begin(String(LA_FRITE_SERVER_URL) + "/next");
  http.setTimeout(HTTP_TIMEOUT_MS);
  int code = http.GET();
  if (code == 204 || code <= 0) { http.end(); return; }
  if (code != 200 || http.getSize() > MAX_JOB_BYTES) { http.end(); drawStatus("bad job"); return; }
  String payload = http.getString();
  http.end();

  JsonDocument doc;                                  // ArduinoJson v7
  if (deserializeJson(doc, payload)) { drawStatus("JSON error"); return; }
  String id    = doc["job"]["id"]    | "";
  String title = doc["job"]["title"] | "Untitled";
  String mode  = doc["job"]["mode"]  | "print";
  String body  = doc["job"]["body"]  | "";
  if (id.isEmpty()) return;

  drawStatus(title);
  if (mode == "animation") playAsciiAnimation(body);
  else                     printTextSlowly(body);
  saveJobToSD(id, title, body);
  ackJob(id);                                        // at-least-once delivery
}

// ============================================================================
//  USB serial jobs: the same @LE_SPOOLER envelope, answered with @ACK <id>
// ============================================================================
static void handleSerialInput() {
#if USB_SERIAL_JOBS
  static String buf;
  static bool inJob = false;
  while (Serial.available()) {
    char c = (char)Serial.read();
    buf += c;
    if (!inJob) {
      if (buf.endsWith("@LE_SPOOLER_BEGIN")) { inJob = true; buf = "@LE_SPOOLER_BEGIN"; }
      else if (buf.length() > 64) buf.remove(0, buf.length() - 32);
      continue;
    }
    if ((int)buf.length() > MAX_JOB_BYTES) {
      Serial.println("@NAK too-large");
      buf = ""; inJob = false;
      return;
    }
    if (buf.endsWith("@LE_SPOOLER_END\n") || buf.endsWith("@LE_SPOOLER_END\r\n")) {
      String id = envelopeHeader(buf, "ID");
      if (id.isEmpty()) id = String("serial-") + millis();
      String body = envelopeBody(buf);
      drawStatus(envelopeHeader(buf, "TITLE"));
      printTextSlowly(body);
      saveJobToSD(id, envelopeHeader(buf, "TITLE"), body);
      Serial.println("@ACK " + id);
      buf = ""; inJob = false;
    }
  }
#endif
}

// ============================================================================
//  Setup & loop
// ============================================================================
static void splashScreen() {
  tft.fillScreen(C_CREAM);
  tft.setTextColor(TFT_DARKGREEN, C_CREAM);
  tft.setTextFont(1);
  tft.setTextSize(3);
  tft.setCursor(30, 110); tft.print("LE");
  tft.setCursor(30, 140); tft.print("SPOOLER");
  tft.setTextSize(1);
  tft.setCursor(30, 180); tft.print("fanfold daisywheel emulator");
  tft.setCursor(30, 195); tft.print("v0.2");
  playBell();
  delay(1500);
}

void setup() {
  Serial.begin(SERIAL_BAUD);
  pinMode(LED_R, OUTPUT); pinMode(LED_G, OUTPUT); pinMode(LED_B, OUTPUT);
  setLed(false, false, true);

  tft.init();
  tft.setRotation(SCREEN_ROTATION);
  pinMode(TFT_BACKLIGHT_PIN, OUTPUT);
  digitalWrite(TFT_BACKLIGHT_PIN, HIGH);

  C_GREEN     = tft.color565(225, 239, 217);   // period greenbar: pale, slightly grey mint
  C_CREAM     = tft.color565(251, 250, 241);   // warm white stock
  C_STRIP     = tft.color565(246, 246, 240);
  C_HOLE      = tft.color565(60, 60, 60);
  C_PERF      = tft.color565(165, 165, 165);
  C_PLATEN    = tft.color565(30, 30, 34);
  C_STATUS_BG = tft.color565(20, 60, 20);
  C_STATUS_FG = tft.color565(180, 255, 180);

#if !TOUCH_USE_LIBRARY
  pinMode(TOUCH_CS, OUTPUT); digitalWrite(TOUCH_CS, HIGH);
  pinMode(TOUCH_CLK, OUTPUT); pinMode(TOUCH_MOSI, OUTPUT);
  pinMode(TOUCH_MISO, INPUT); pinMode(TOUCH_IRQ, INPUT);
#else
  touchSPI.begin(TOUCH_CLK, TOUCH_MISO, TOUCH_MOSI, TOUCH_CS);
  ts.begin(touchSPI);
#endif

  randomSeed(esp_random());
  splashScreen();

#if SD_ENABLED
  sdSPI.begin(SD_SCK, SD_MISO, SD_MOSI, SD_CS);
  sdOk = SD.begin(SD_CS, sdSPI);
#endif

  memset(lines, 0, sizeof(lines));
  drawPaper();
  connectWiFi();
  if (wifiOk) drawStatus(checkHealth() ? "READY" : "spooler offline");
  setLed(false, false, false);
  Serial.println("@LE_SPOOLER_READY");
}

void loop() {
  handleSerialInput();

  if (wifiOk && millis() - lastPoll > POLL_INTERVAL_MS) {
    lastPoll = millis();
    pollNext();
  }

  // Touch UI (very small for now):
  //   top third    -> browse saved documents
  //   middle third -> play an animation from the server
  //   bottom third -> form feed
  int x, y;
  if (readTouch(x, y)) {
    delay(200);
    if (y < SCREEN_H / 3)          browseDocuments();
    else if (y < 2 * SCREEN_H / 3) playAnimationFromServer("robot_wave");
    else                           formFeed();
  }
  delay(10);
}
