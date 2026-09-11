# Two Cups Workbook Studio

Turn a podcast SRT into a simple, printable English worksheet — then edit every
line of it in the browser. The worksheet is written entirely in English for the
learner; the studio interface switches between English and Korean with the
language button.

The frontend is one HTML file and runs on its own. The backend is one Node file
with no dependencies: run it and the studio shares its subtitles and saves the
worksheets you edit.

## Run it

**Frontend only** — open `public/index.html` in a browser. CUP01–CUP15 are
built into `public/episodes.js`, so a worksheet is on screen straight away, and
worksheets are kept in that browser.

**With the backend** — Node 18 or newer:

```bash
npm start                 # → http://localhost:8080
PORT=3000 npm start       # another port
npm run dev               # restart on change
docker build -t workbook-studio . && docker run -p 8080:8080 workbook-studio
```

Then subtitles come from `data/srt/` (drop new `.srt` files in, or drag them
onto the page and they are saved there) and every edit is written to
`data/sheets/<EPISODE>.json`. The left rail says which of the two you are in.

## Use it

1. Pick an episode, a level and a length on the left, then **Make worksheet**.
   **Whole script** next to it makes a copying sheet of the entire episode —
   every line, in order, each with a line to write it again.
2. Every word on the sheet is editable. Each part has ↑ ↓ move, ↻ make again,
   ＋ add an item, ✕ delete.
3. The **Script** panel on the right turns any sentence into a copy line, a
   blank, a dictation line, a repeat line, a T/F item, a matching pair or a
   word-order item. Link an audio file and every time code becomes a play
   button (with **Loop** to play one sentence only).
4. **Print · PDF** → choose “Save as PDF”. The answer key prints on its own page.

## What gets made

| Part | The task | How it is built |
| --- | --- | --- |
| Before You Listen | predict, tick known words, one question | from the episode topic line |
| Copy the Script | read a line, write the same line under it | a real run of the script, shortest passage first |
| Words to Know | word · meaning · example, with a tick box | stemmed frequency, stopwords and names removed |
| Useful Phrases | phrases said again and again, 3 repeat boxes | 2–4 word n-grams, the longer form kept |
| Listen and Write the Word | one blank per sentence + word box | key words first, spread so no word repeats |
| True or False | short statements to circle | half made false by an opposite, a number or a negation |
| Match the Two Halves | join sentence halves with a letter | split near the middle at and / but / when / with … |
| Put the Words in Order | 4–7 word sentences, shuffled | statements only, questions left out |
| Listen and Write the Sentence | time code + writing lines | short sentences, spread across the episode |
| Say It with Ben and Mia | shadowing lines, 3 boxes each | short sentences with time codes |
| Your Turn | write and speak | built from the topic and the phrases above |
| Choose the Right Word · Notes | optional extras | off by default |

Level (Beginner A1 / Elementary A2 / Intermediate B1) sets sentence length,
blanks per sentence and whether a word box is given. Length (10 / 20 / 35 min)
sets how many items each part has.

## Layout

```
public/index.html        the whole studio: markup, styles, logic
public/episodes.js       built-in transcripts (made from data/srt)
server.mjs               static server + JSON API, no dependencies
data/srt/                subtitle files CUP01-CUP15
data/sheets/             saved worksheets, one JSON per episode
tools/build-episodes.mjs bundle data/srt into public/episodes.js
tools/make-artifact.mjs  body-only copy for publishing on the web
tools/make-zip.mjs       pack everything into dist/two-cups-workbook-studio.zip
```

## API

| | |
| --- | --- |
| `GET /api/health` | `{ ok, episodes, sheets }` |
| `GET /api/episodes` | every subtitle file with its text |
| `GET · PUT · DELETE /api/episodes/:code` | one subtitle file (`PUT` body = SRT text) |
| `GET /api/sheets` | saved worksheets with title and time |
| `GET · PUT · DELETE /api/sheets/:code` | one worksheet (`PUT` body = worksheet JSON) |

Codes are matched against `^[A-Za-z0-9][A-Za-z0-9._-]{0,63}$`, bodies are capped
at 8 MB, and static files are served from `public/` only. There is no login: run
it on your own machine or on a network you trust, and put it behind a proxy with
authentication before exposing it.

---

학습지 내용은 학습자를 위해 전부 영어로 나오고, 스튜디오 화면은 오른쪽 위
언어 버튼으로 English / 한국어를 바꿀 수 있습니다. `npm start`로 서버를 켜면
자막은 `data/srt/`, 학습지는 `data/sheets/`에 저장됩니다.
