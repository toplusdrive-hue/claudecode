# Two Cups Workbook Studio

Turn a podcast SRT into a simple, printable English worksheet — then edit every
line of it in the browser. One HTML file, no server, no build.
The worksheet is written entirely in English for learners; the studio interface
itself can switch between English and Korean with the language button.

## Use it

1. Open `index.html` in a browser. (`episodes.js` carries CUP01–CUP15, so a
   worksheet is on screen straight away.)
2. Pick an episode, a level and a length on the left, then **Make worksheet**.
3. Every word on the sheet is editable. Each part has ↑ ↓ move, ↻ make again,
   ＋ add an item, ✕ delete.
4. The **Script** panel on the right turns any sentence into a blank, a
   dictation line, a repeat line, a T/F item, a matching pair or a word-order item.
5. **Print · PDF** → choose “Save as PDF”. The answer key prints on its own page.

Drop your own `.srt` / `.vtt` files on the left to use any other transcript.
Link an audio file and every time code becomes a play button (with a Loop
button that plays one sentence only).

## What gets made

| Part | The task | How it is built |
| --- | --- | --- |
| Before You Listen | predict, tick known words, one question | from the episode topic line |
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

Level (Beginner A1 / Elementary A2 / Intermediate B1) sets how long the
sentences are, how many blanks each one gets, and whether a word box is given.
Length (10 / 20 / 35 min) sets how many items each part has.

## Files

- `index.html` — the whole studio (HTML, CSS, JS in one file)
- `episodes.js` — built-in transcripts, made by `build-episodes.mjs`
- `srt/` — source subtitles CUP01–CUP15
- `build-episodes.mjs` — bundle a folder of SRT files: `node build-episodes.mjs`
- `make-artifact.mjs` — body-only copy for publishing on the web

Worksheets autosave in the browser; **Save** writes a JSON file and **Open**
reads it back. **Export HTML** and **Copy as text** give you the sheet to paste
into any other document.

---

학습지 내용은 학습자를 위해 전부 영어로 나오고, 스튜디오 화면은 오른쪽 위
언어 버튼으로 English / 한국어를 바꿀 수 있습니다.
