# 착수 전 조사 기록 (2절)

조사 일자: 2026-09-14
조사 환경: Linux 컨테이너, Python 3.11.15, `pycapcut==0.0.3` 휠 실물 해체
대상 패키지: `pycapcut-0.0.3-py3-none-any.whl` (PyPI, 343KB, 36개 모듈)

> **이 문서의 신뢰 등급**
> - **[실측]** — pycapcut 0.0.3 소스를 직접 읽어 확인. 재조사 불필요.
> - **[미검증]** — 윈도우/캡컷 실물이 있어야 확인 가능. 이 조사 환경에는 캡컷도
>   윈도우도 없으므로 **확인하지 못했습니다.** 최초 실행 시 사용자가 확인해야 합니다.

---

## 1. pycapcut 실물 API 확인 [실측]

### 1.1 패키지 구성

```
pycapcut/
  __init__.py            공개 API 재수출
  draft_folder.py        DraftFolder
  script_file.py         ScriptFile, ScriptMaterial   (808줄, 핵심)
  segment.py             BaseSegment, MediaSegment, VisualSegment, ClipSettings, Speed
  video_segment.py       VideoSegment, StickerSegment, Transition, Mask, Filter, BackgroundFilling
  text_segment.py        TextSegment, TextStyle, TextBorder, TextBackground, TextBubble
  audio_segment.py       AudioSegment, AudioFade, AudioEffect
  local_materials.py     VideoMaterial, AudioMaterial, CropSettings
  track.py               Track, TrackType, Track_meta
  time_util.py           Timerange, tim(), trange(), srt_tstamp(), SEC
  template_mode.py       ImportedTrack, EditableTrack, ShrinkMode, ExtendMode
  jianying_controller.py JianyingController (자동 내보내기)
  metadata/              *Type enum 정의 (13개 파일)
  assets/                draft_content_template.json, draft_meta_info.json
```

의존성: `pymediainfo`, `imageio`, `uiautomation>=2` (win32 한정). Requires-Python `>=3.8`.

### 1.2 확인한 시그니처

```python
DraftFolder(folder_path: str)
  .list_drafts() -> List[str]                 # 하위 폴더명을 그대로 나열 (형식 검사 없음)
  .has_draft(name) -> bool
  .remove(name) -> None                       # shutil.rmtree
  .create_draft(name, width, height, fps=30, *, allow_replace=False) -> ScriptFile
  .load_template(name) -> ScriptFile
  .duplicate_as_template(template, new_name, allow_replace=False) -> ScriptFile
  .inspect_material(name) -> None             # stdout 출력만

ScriptFile(width, height, fps=30)
  .load_template(json_path) -> ScriptFile     # staticmethod
  .add_material(VideoMaterial | AudioMaterial) -> self
  .add_track(track_type, track_name=None, *, mute=False,
             relative_index=0, absolute_index=None) -> self
  .add_segment(segment, track_name=None) -> self
  .add_effect(effect, t_range, track_name=None, *, params=None) -> self
  .add_filter(filter_meta, t_range, track_name=None, intensity=100.0) -> self
  .import_srt(srt_path, track_name, *, ...) -> self
  .get_imported_track(track_type, name=None, index=None) -> EditableTrack
  .import_track(source_file, track, *, offset=0, new_name=None) -> self
  .replace_material_by_name / by_seg / .replace_text(...)
  .dumps() -> str   .dump(path)   .save()

VideoSegment(material, target_timerange, *, source_timerange=None,
             speed=None, volume=1.0, clip_settings=None)
  .add_transition(transition_type, *, duration=None) -> self
  .add_animation / .add_effect / .add_filter / .add_mask / .add_background_filling

TextSegment(text, timerange, *, font=None, style=None,
            clip_settings=None, border=None, background=None)
  .create_from_template(text, timerange, template) -> TextSegment   # classmethod
  .add_animation / .add_bubble / .add_effect
  .export_material() -> Dict

ClipSettings(*, alpha=1.0, flip_horizontal=False, flip_vertical=False,
             rotation=0.0, scale_x=1.0, scale_y=1.0,
             transform_x=0.0, transform_y=0.0)
TextStyle(*, size=8.0, bold=False, italic=False, underline=False,
          color=(1,1,1), alpha=1.0, align=0, vertical=False,
          letter_spacing=0, line_spacing=0,
          auto_wrapping=False, max_line_width=0.82)
TextBorder(*, alpha=1.0, color=(0,0,0), width=40.0)     # width는 /100*0.2로 재매핑됨
TextBackground(*, color, style=1, alpha=1.0, round_radius=0.0,
               height=0.14, width=0.14,
               horizontal_offset=0.5, vertical_offset=0.5)
               # offset은 *2-1로 재매핑됨
Timerange(start_us, duration_us)   # end는 property. trange("1m2s","0.5s")도 가능
```

### 1.3 좌표·시간 단위 확인

`ClipSettings.transform_x/y` 독스트링 원문: *"水平位移, 单位为半个画布宽"* — **캔버스 절반 폭/높이가 1인 정규화 좌표**. 요청서 3.2절과 일치.

`Timerange` 전 구간 마이크로초 정수. `SEC = 1000000`.

`VideoMaterial.duration`은 `int(info.video_tracks[0].duration * 1e3)` — pymediainfo가 **밀리초**로 읽은 값에 1000을 곱합니다. 그래서 ffprobe의 초 단위 값과 마지막 자리가 어긋납니다(요청서 3.8절).

---

## 2. 요청서 3절 항목별 대조 결과

| 절 | 주장 | 대조 결과 |
|---|---|---|
| 3.2 | transform은 정규화 좌표, 위가 양수 | **[실측] 일치** — 독스트링 "单位为半个画布宽" |
| 3.3 | pyCapCut은 `track_render_index`를 전부 0으로 둠 | **[실측] 일치** — `segment.py:68`, `video_segment.py:140`에 `"track_render_index": 0` 하드코딩. 이 값은 트랙이 아니라 **세그먼트마다** 기록됨 |
| 3.4 | `check_flag` 7 / +8 테두리 / +16 배경 | **[실측] 일치** — `text_segment.py::export_material`가 `check_flag=7`에서 시작해 `border`면 `|=8`, `background`면 `|=16` |
| 3.5 | pyCapCut 텍스트 소재에 필드가 대거 빠짐 | **[실측] 일치** — `export_material()`이 내보내는 키는 **15개**뿐. `shadow_*`, `font_*`, `text_color`, `words`, `border_mode` 등은 소스에 주석 처리되어 있음(의도적 생략) |
| 3.6 | 폰트 path에 더미 문자열 | **[실측] 일치** — `"path": "C:/%s.ttf" % self.font.name`. `FontType` 멤버 **348개**, Pretendard **없음** (확인) |
| 3.7 | `TransitionType` 1137개, 이름 중국어 | **[실측] 일치** — enum 멤버 정확히 **1137개**. `Transition.effect_id`는 `effect_meta.value.effect_id`이므로 **effect_id 역조회 가능** |
| 3.8 | source_timerange가 소재 길이를 1µs라도 넘으면 거부 | **[실측] 일치** — `video_segment.py`: `if source_timerange.end > material.duration: raise ValueError(f"截取的素材时间范围 {…} 超出了素材时长({…})")` |
| 3.9 | 세그먼트 겹치면 거부 | **[실측] 일치** — `track.py::add_segment`가 `SegmentOverlap("New segment overlaps with existing segment [start: …, end: …]")` |
| 3.10 | `dumps()`가 ratio를 항상 "original"로 씀 | **[실측] 일치** — `script_file.py:778` `{"width":…, "height":…, "ratio":"original"}` 하드코딩 |
| 3.11 | `draft_meta_info.json` 경로 신뢰 불가 | **[실측] 일치** — 번들 템플릿의 `draft_fold_path`와 `draft_name`이 **빈 문자열**이고, `create_draft`는 템플릿을 그대로 복사할 뿐 채우지 않음 |
| 3.12 | `create_draft`가 레지스트리에 등록 안 함 | **[실측] 일치** — `create_draft`는 `os.makedirs` + `shutil.copy(DRAFT_META_TEMPLATE)` + `ScriptFile` 생성이 전부. `root_meta_info.json`을 건드리는 코드가 패키지 전체에 **없음** (`grep root_meta_info` 결과 0건) |

3.13~3.19는 캡컷/윈도우/ffmpeg 실물이 필요한 항목이라 이 환경에서 **재확인하지 못했습니다.**
요청서의 실측값을 그대로 신뢰하고 구현했습니다.

---

## 3. 요청서에 없던 추가 발견 [실측]

### 3.1 `draft_meta_info.json` 템플릿의 `draft_id`가 고정 상수

```json
"draft_id": "792BD5DA-E961-4821-B10E-F51E4683DEC0"
```

`create_draft`는 이 템플릿을 **그대로 복사**합니다. 따라서 pyCapCut으로 만든
**모든 드래프트가 같은 `draft_id`를 갖습니다.** 캡컷이 드래프트를 id로 식별하는
경우 목록이 섞이거나 덮어써질 수 있습니다.

→ 대응: `capcut_draft.finalize_draft()`에서 드래프트마다 **새 UUID를 생성해 주입**합니다.
(`draft_id`, `draft_content.json`의 `id`, `draft_meta_info.json`의 `draft_id`)

### 3.2 자동 내보내기(`JianyingController`)는 현재 구성에서 동작하지 않을 가능성이 매우 높음

세 가지 문제를 소스에서 확인했습니다.

1. **창 제목이 중국어로 하드코딩**
   ```python
   def __jianying_window_cmp(self, control, depth):
       if control.Name != "CapCut专业版":   # ← 한국어/영어 캡컷은 "CapCut"
           return False
   ```
   한국어 UI에서는 절대 매칭되지 않고 `AutomationError("CapCut窗口未找到")`로 끝납니다.
   내보내기 창도 `Name="导出"`로 찾습니다.

2. **지원 버전 명시**: `export_draft` 독스트링에 *"目前仅支持CapCut6及以下版本"*
   (**캡컷 6 이하만 지원**). 검증 대상은 9.3.0.3969입니다.

3. **애초에 `import pycapcut`으로는 접근할 수 없음**
   ```python
   ISWIN = (sys.platform == 'win32')
   if ISWIN:
       pass          # ← 아무것도 import 하지 않음
   ```
   `__all__`에도 없습니다. 쓰려면 `from pycapcut.jianying_controller import JianyingController`로
   **직접 임포트**해야 합니다.

→ 대응: 4차 자동 내보내기는 **기본 OFF + 확인 체크박스 필수**로 두고, UI에
위 3가지를 그대로 표시합니다. 직접 임포트 경로를 사용하며, 실패해도
"캡컷에서 직접 내보내기" 경로가 항상 살아 있게 했습니다.

### 3.3 세그먼트의 `render_index`와 `track_render_index`는 다른 값

- `render_index` — `Track.export_json()`이 **트랙의 render_index를 세그먼트마다 복사**.
  `TrackType`별 기본값: video 0, audio 0, effect 10000, filter 11000, sticker 14000, **text 15000**.
- `track_render_index` — `BaseSegment.export_json()`이 **항상 0**.

요청서 3.3절이 지적한 건 후자입니다. 전자는 pyCapCut이 이미 올바르게 채웁니다.
→ 후처리는 `track_render_index`만 건드리고 `render_index`는 그대로 둡니다.

### 3.4 `dumps()`는 트랙을 `render_index` 기준으로 정렬

```python
track_list = list(self.imported_tracks + list(self.tracks.values()))
track_list.sort(key=lambda track: track.render_index)
```

`sort`는 안정 정렬이므로 **같은 render_index면 "가져온 트랙 → 새 트랙" 순서**가 유지됩니다.
후처리에서 `track_render_index`를 다시 매길 때는 이 **최종 정렬 결과 순서**를 기준으로
해야 합니다. 따라서 교정은 반드시 **`save()` 이후 파일을 다시 읽어** 수행합니다.

### 3.5 `TextBorder`와 `TextBackground`는 입력값을 재매핑함

```python
self.width = width / 100.0 * 0.2                 # TextBorder
self.horizontal_offset = horizontal_offset * 2 - 1   # TextBackground
self.vertical_offset   = vertical_offset   * 2 - 1
```

캘리브레이션으로 읽어온 **드래프트 원시값**을 그대로 생성자에 넣으면 **두 번 변환됩니다.**
→ 대응: 캘리브레이션 값은 pyCapCut 생성자를 거치지 않고 **후처리에서 소재 JSON에 직접
주입**합니다. (구조는 pyCapCut, 스타일은 후처리 — 요청서 3.6절의 2단 구성과 동일한 이유)

---

## 4. 이 환경에서 확인하지 못한 것 [미검증]

| 항목 | 사유 |
|---|---|
| 캡컷 드래프트 폴더 실제 경로 | 윈도우/캡컷 없음. 자동 탐지 + 수동 지정 폴백으로 구현 |
| 사용자 드래프트의 `draft_content.json` 실제 필드 구성 | 실물 드래프트 없음. **캘리브레이션이 기본 경로**인 이유 |
| 텍스트 소재 126개 필드 목록 | 위와 동일. 캘리브레이션이 원본을 통째로 저장하므로 목록을 알 필요가 없게 설계 |
| 3.13 캡컷 프로세스 락 동작 | 캡컷 없음 |
| 3.14 ffmpeg stderr 4KB 데드락 | 이 컨테이너의 ffmpeg 배너 길이가 다름. **요청서 실측값을 신뢰**하고 `-hide_banner` + 별도 스레드 배수를 **둘 다** 적용 |
| 3.15/3.16 cp949·bat 인코딩 | 리눅스. 요청서 지시대로 구현하고 bat은 CP949로 기록 |
| 3.19 faster-whisper CPU 동작 / 모델 다운로드 | 모델 미설치 |
| 세로 오버레이 scale 1.8 / y -0.078125 | 실물 확인 불가. 요청서 실측값 채택 |

이 표의 항목은 `README.md`의 **미검증 항목**에 그대로 옮겨 적었습니다.
