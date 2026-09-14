/* 캡컷 자동 편집기 — 빌드 도구 없는 순수 JS 단일 페이지 */
'use strict';

const US = 1000000;

/* ── 유틸 ────────────────────────────────────────────────────────────── */
function h(tag, attrs, ...kids) {
  const node = document.createElement(tag);
  for (const [key, value] of Object.entries(attrs || {})) {
    if (value === null || value === undefined || value === false) continue;
    if (key === 'class') node.className = value;
    else if (key === 'html') node.innerHTML = value;
    else if (key.startsWith('on')) node.addEventListener(key.slice(2).toLowerCase(), value);
    else if (key === 'dataset') Object.assign(node.dataset, value);
    else if (value === true) node.setAttribute(key, '');
    else node.setAttribute(key, value);
  }
  for (const kid of kids.flat(3)) {
    if (kid === null || kid === undefined || kid === false) continue;
    node.appendChild(typeof kid === 'object' ? kid : document.createTextNode(String(kid)));
  }
  return node;
}

function tc(us) {
  const ms = Math.max(0, Math.round(us / 1000));
  const s = Math.floor(ms / 1000);
  const pad = (n) => String(n).padStart(2, '0');
  return `${pad(Math.floor(s / 3600))}:${pad(Math.floor((s % 3600) / 60))}:${pad(s % 60)}.${String(ms % 1000).padStart(3, '0')}`;
}
function secs(us) { return (us / US).toFixed(2) + '초'; }
function mmss(us) {
  const s = Math.floor(us / US);
  return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}

function errorText(payload) {
  if (!payload) return '알 수 없는 오류입니다.';
  const detail = payload.detail !== undefined ? payload.detail : payload;
  if (typeof detail === 'string') return detail;
  if (detail && detail.message) return detail.message;
  return JSON.stringify(detail);
}

async function api(path, options) {
  const opts = Object.assign({ headers: {} }, options || {});
  if (opts.body !== undefined && typeof opts.body !== 'string') {
    opts.headers['Content-Type'] = 'application/json';
    opts.body = JSON.stringify(opts.body);
    opts.method = opts.method || 'POST';
  }
  const res = await fetch(path, opts);
  const text = await res.text();
  let payload = null;
  try { payload = text ? JSON.parse(text) : null; } catch (_) { payload = text; }
  if (!res.ok) {
    const err = new Error(errorText(payload));
    err.payload = payload;
    err.status = res.status;
    throw err;
  }
  return payload;
}

let toastTimer = null;
function toast(message, kind) {
  const root = document.getElementById('toastRoot');
  root.innerHTML = '';
  root.appendChild(h('div', { class: 'toast ' + (kind || '') }, message));
  clearTimeout(toastTimer);
  toastTimer = setTimeout(() => { root.innerHTML = ''; }, kind === 'err' ? 12000 : 5000);
}

function closeModal() { document.getElementById('modalRoot').innerHTML = ''; }
function modal(title, bodyNodes, footerNodes) {
  const root = document.getElementById('modalRoot');
  root.innerHTML = '';
  const backdrop = h('div', {
    class: 'modal-backdrop',
    onclick: (ev) => { if (ev.target === backdrop) closeModal(); },
  }, h('div', { class: 'modal' },
    h('h2', {}, title),
    ...(Array.isArray(bodyNodes) ? bodyNodes : [bodyNodes]),
    h('div', { class: 'btn-row', style: 'margin-top:var(--sp-5)' },
      ...(footerNodes || []), h('button', { class: 'btn', onclick: closeModal }, '닫기')),
  ));
  root.appendChild(backdrop);
}

function banner(kind, title, lines) {
  const items = (lines || []).filter(Boolean);
  return h('div', { class: 'banner ' + kind },
    title ? h('strong', {}, title) : null,
    items.length === 1 ? items[0] : (items.length ? h('ul', {}, items.map((t) => h('li', {}, t))) : null));
}

/* ── 상태 ────────────────────────────────────────────────────────────── */
const S = {
  status: null,
  sessionId: null,
  session: null,
  steps: [],
  active: 'stage0',
  cuts: null,
  subtitles: [],
  profile: null,
  jobs: {},
  logSeq: 0,
};

/* ── 작업(job) 진행률 ─────────────────────────────────────────────────── */
function progressBox() {
  return h('div', { class: 'progress hidden' },
    h('div', { class: 'progress-bar' }, h('i', { style: 'width:0%' })),
    h('div', { class: 'progress-meta' },
      h('span', { class: 'label' }, ''),
      h('span', { class: 'pct mono' }, '0%'),
      h('button', { class: 'btn sm danger cancel' }, '취소')),
    h('div', { class: 'warnings' }));
}

function runJob(job, box, onDone) {
  box.classList.remove('hidden');
  const bar = box.querySelector('.progress-bar i');
  const label = box.querySelector('.label');
  const pct = box.querySelector('.pct');
  const warnBox = box.querySelector('.warnings');
  const cancelBtn = box.querySelector('.cancel');
  cancelBtn.onclick = () => api(`/api/jobs/${job.id}/cancel`, { method: 'POST' })
    .then((r) => toast(r.message))
    .catch((e) => toast(e.message, 'err'));

  const source = new EventSource(`/api/jobs/${job.id}/stream`);
  const finish = (payload) => {
    source.close();
    if (payload.status === 'done') { onDone(payload); }
    else if (payload.status === 'failed') { toast(payload.error || '작업이 실패했습니다.', 'err'); }
    else { toast('작업을 취소했습니다.'); }
    setTimeout(() => box.classList.add('hidden'), payload.status === 'done' ? 1200 : 6000);
  };

  source.onmessage = (ev) => {
    let payload;
    try { payload = JSON.parse(ev.data); } catch (_) { return; }
    if (payload.error) { source.close(); toast(payload.error, 'err'); return; }
    bar.style.width = Math.round(payload.progress * 100) + '%';
    label.textContent = `${payload.label || ''}  ·  ${payload.elapsed_sec}초 경과`;
    pct.textContent = Math.round(payload.progress * 100) + '%';
    warnBox.innerHTML = '';
    if (payload.warnings && payload.warnings.length) {
      warnBox.appendChild(banner('warn', '확인이 필요합니다', payload.warnings));
    }
    if (payload.status !== 'running') finish(payload);
  };
  source.onerror = () => {
    source.close();
    api(`/api/jobs/${job.id}`).then((payload) => {
      if (payload.status !== 'running') finish(payload);
      else toast('진행률 연결이 끊겼습니다. 작업은 계속 돌고 있습니다. 잠시 후 화면을 새로고침해 주세요.', 'err');
    }).catch(() => {});
  };
}

/* ── 네비게이션 ──────────────────────────────────────────────────────── */
function renderNav() {
  const nav = document.getElementById('nav');
  nav.innerHTML = '';
  for (const step of S.steps) {
    const cls = ['nav-item'];
    if (step.id === S.active) cls.push('active');
    if (step.done) cls.push('done');
    const btn = h('button', {
      class: cls.join(' '),
      disabled: step.locked,
      title: step.locked ? step.lock_reason : (step.done ? `완료: ${step.done_at}` : ''),
      onclick: () => { S.active = step.id; render(); },
    },
      h('span', { class: 'ni-icon' }, step.icon),
      h('span', { class: 'ni-body' },
        h('span', { class: 'ni-title' }, step.title),
        step.locked ? h('span', { class: 'ni-note' }, step.lock_reason) : null,
        step.done && !step.locked ? h('span', { class: 'ni-note' }, '완료') : null),
      h('span', { class: 'ni-dot' }));
    nav.appendChild(btn);
  }
}

/* ── 경로 선택 위젯 (세 가지 방식) ───────────────────────────────────── */
function pathPicker({ kind, multi, onPick, label }) {
  const wrap = h('div', {});
  const pasteBox = h('textarea', {
    placeholder: '경로를 붙여넣으세요. 여러 개면 줄바꿈으로 구분합니다.\n폴더를 넣으면 그 안의 파일 목록으로 펼쳐 드립니다.',
    rows: 3,
  });
  const feedback = h('div', {});
  const browserBox = h('div', { class: 'hidden' });

  async function expand(paths) {
    try {
      const res = await api('/api/files/expand', { body: { paths, kind } });
      feedback.innerHTML = '';
      if (res.problems && res.problems.length) {
        feedback.appendChild(banner('warn', '확인이 필요한 경로', res.problems.map((p) => {
          const sug = (p.suggestions || []).length ? ` 혹시 이건가요? ${p.suggestions[0]}` : '';
          return p.message + sug;
        })));
      }
      if (res.files.length) onPick(multi ? res.files : res.files.slice(0, 1));
      else if (!res.problems.length) feedback.appendChild(banner('warn', null, ['고른 경로에서 파일을 찾지 못했습니다.']));
    } catch (e) { toast(e.message, 'err'); }
  }

  async function openBrowser(path) {
    browserBox.classList.remove('hidden');
    browserBox.innerHTML = '<p class="muted">목록을 읽는 중…</p>';
    let data;
    try { data = await api(`/api/files/browse?kind=${kind}&path=${encodeURIComponent(path || '')}`); }
    catch (e) { browserBox.innerHTML = ''; browserBox.appendChild(banner('danger', null, [e.message])); return; }

    const picked = new Set();
    const fileList = h('div', { class: 'browser-main' });
    for (const dir of data.dirs) {
      fileList.appendChild(h('div', { class: 'browser-entry', onclick: () => openBrowser(dir.path) },
        h('span', {}, '📁'), h('span', { class: 'grow' }, dir.name)));
    }
    for (const file of data.files) {
      const row = h('div', { class: 'browser-entry' },
        h('span', {}, '🎬'), h('span', { class: 'grow' }, file.name),
        h('span', { class: 'faint' }, (file.size / 1048576).toFixed(1) + 'MB'));
      row.onclick = () => {
        if (!multi) { onPick([file.path]); browserBox.classList.add('hidden'); return; }
        if (picked.has(file.path)) { picked.delete(file.path); row.classList.remove('selected'); }
        else { picked.add(file.path); row.classList.add('selected'); }
      };
      fileList.appendChild(row);
    }
    if (!data.dirs.length && !data.files.length) {
      fileList.appendChild(h('div', { class: 'browser-entry' }, h('span', { class: 'muted' }, '이 폴더에는 표시할 항목이 없습니다.')));
    }

    const side = h('div', { class: 'browser-side' },
      h('div', { class: 'side-title' }, '바로 가기'),
      ...data.shortcuts.map((s) => h('button', { onclick: () => openBrowser(s.path) }, s.label)),
      h('div', { class: 'side-title' }, '드라이브'),
      ...data.drives.map((d) => h('button', { onclick: () => openBrowser(d.path) }, d.label)));

    browserBox.innerHTML = '';
    if (data.error) browserBox.appendChild(banner('warn', null, [data.error]));
    browserBox.appendChild(h('div', { class: 'btn-row', style: 'margin-bottom:var(--sp-2)' },
      data.parent ? h('button', { class: 'btn sm', onclick: () => openBrowser(data.parent) }, '⬆ 상위 폴더') : null,
      h('span', { class: 'mono faint' }, data.path),
      multi ? h('button', {
        class: 'btn sm primary', onclick: () => {
          if (!picked.size) { toast('파일을 하나 이상 고르세요.'); return; }
          onPick([...picked]); browserBox.classList.add('hidden');
        }
      }, '선택 완료') : null,
      h('button', { class: 'btn sm', onclick: () => browserBox.classList.add('hidden') }, '닫기')));
    browserBox.appendChild(h('div', { class: 'browser' }, side, fileList));
  }

  wrap.appendChild(h('div', { class: 'btn-row', style: 'margin-bottom:var(--sp-3)' },
    h('button', { class: 'btn', onclick: () => openBrowser('') }, '앱에서 찾아보기'),
    h('button', {
      class: 'btn', onclick: async (ev) => {
        const btn = ev.currentTarget; btn.disabled = true; btn.textContent = '창을 띄우는 중…';
        try {
          const res = await api('/api/files/native-pick', { body: { mode: 'files', kind } });
          if (!res.ok) { toast(res.error, 'err'); }
          else if (res.paths.length) { onPick(multi ? res.paths : res.paths.slice(0, 1)); }
          if (res.warning) toast(res.warning, 'err');
        } catch (e) { toast(e.message, 'err'); }
        finally { btn.disabled = false; btn.textContent = '윈도우 기본 창으로 고르기'; }
      }
    }, '윈도우 기본 창으로 고르기'),
    label ? h('span', { class: 'faint' }, label) : null));
  wrap.appendChild(browserBox);
  wrap.appendChild(h('label', { class: 'field' }, h('span', {}, '또는 경로 직접 붙여넣기'), pasteBox));
  wrap.appendChild(h('div', { class: 'btn-row', style: 'margin-bottom:var(--sp-3)' },
    h('button', {
      class: 'btn sm', onclick: () => {
        const lines = pasteBox.value.split('\n').map((l) => l.trim()).filter(Boolean);
        if (!lines.length) { toast('경로를 입력해 주세요.'); return; }
        expand(lines);
      }
    }, '경로 확인하고 추가')));
  wrap.appendChild(feedback);
  return wrap;
}

/* ── 0차 프로젝트 준비 ───────────────────────────────────────────────── */
function viewStage0() {
  const view = document.getElementById('view');
  const sources = (S.session.sources || []).slice();
  const card = h('div', { class: 'card' });
  card.appendChild(h('h2', {}, '0차 프로젝트 준비'));
  card.appendChild(h('p', {}, '원본 영상을 순서대로 고릅니다. 여러 개를 넣으면 하나로 이어 붙인 타임라인으로 다룹니다.'));

  const env = S.status;
  if (env) {
    if (env.blocks.length) card.appendChild(banner('danger', '먼저 해결해야 합니다', env.blocks));
    if (env.warnings.length) card.appendChild(banner('warn', '검증된 조합과 다릅니다', env.warnings));
    if (env.capcut.running) {
      card.appendChild(banner('danger', '캡컷이 실행 중입니다',
        [`드래프트를 건드리는 작업은 캡컷을 완전히 종료해야 합니다. 캡컷은 프로젝트를 메모리에 들고 있다가 저장할 때 파일을 통째로 덮어씁니다. (감지된 프로세스 ${env.capcut.process_count}개)`]));
    }
    if (env.whisper.notice) card.appendChild(banner('info', '음성 인식 모델 안내', [env.whisper.notice]));

    if (!env.ffmpeg || !env.ffprobe) {
      const input = h('input', { type: 'text', placeholder: 'C:\\ffmpeg\\bin' });
      const result = h('div', {});
      card.appendChild(h('div', { class: 'banner warn' },
        h('strong', {}, 'ffmpeg 폴더를 직접 지정'),
        h('div', { class: 'faint', style: 'margin-bottom:var(--sp-2)' },
          '압축을 푼 폴더 경로를 넣어 주세요. C:\\ffmpeg 처럼 바깥 폴더를 넣어도 ' +
          '안쪽의 ffmpeg-9.x-essentials_build\\bin 을 찾아냅니다. ' +
          '아직 없으면 gyan.dev/ffmpeg/builds 에서 essentials 빌드를 받으십시오.'),
        input,
        h('div', { class: 'btn-row', style: 'margin-top:var(--sp-2)' },
          h('button', {
            class: 'btn sm', onclick: async () => {
              try {
                const res = await api('/api/setup/media-tools', { body: { path: input.value } });
                toast('ffmpeg 경로를 저장했습니다.', 'ok');
                result.innerHTML = '';
                result.appendChild(h('div', { class: 'faint mono' }, res.ffmpeg));
                await boot();
              } catch (e) {
                result.innerHTML = '';
                result.appendChild(banner('danger', null, [e.message]));
              }
            }
          }, '저장하고 확인'),
          h('button', {
            class: 'btn sm', onclick: async () => {
              const res = await api('/api/files/native-pick', { body: { mode: 'folder' } }).catch(() => null);
              if (res && res.ok && res.paths.length) input.value = res.paths[0];
              else if (res && res.error) toast(res.error, 'err');
            }
          }, '폴더 찾아보기')),
        result));
    }

    if (env.capcut.running) {
      card.appendChild(h('div', { class: 'btn-row', style: 'margin-bottom:var(--sp-3)' },
        h('button', {
          class: 'btn sm', onclick: async () => {
            const res = await api('/api/setup/capcut-running').catch(() => null);
            if (!res) return;
            toast(res.message, res.running ? 'err' : 'ok');
            if (!res.running) await boot();
          }
        }, '캡컷을 껐습니다 — 다시 확인'),
        h('button', { class: 'btn sm', onclick: () => boot() }, '환경 전체 다시 확인')));
    }

    if (!env.draft_root) {
      const input = h('input', { type: 'text', placeholder: 'C:\\Users\\...\\com.lveditor.draft' });
      card.appendChild(h('div', { class: 'banner warn' },
        h('strong', {}, '캡컷 드래프트 폴더를 직접 지정'),
        input,
        h('div', { class: 'btn-row', style: 'margin-top:var(--sp-2)' },
          h('button', {
            class: 'btn sm', onclick: async () => {
              try { await api('/api/setup/draft-root', { body: { path: input.value } }); toast('드래프트 폴더를 저장했습니다.', 'ok'); await boot(); }
              catch (e) { toast(e.message, 'err'); }
            }
          }, '저장'))));
    }
  }

  const listBox = h('div', {});
  function renderList() {
    listBox.innerHTML = '';
    if (!sources.length) {
      listBox.appendChild(h('p', { class: 'muted' }, '아직 고른 영상이 없습니다.'));
      return;
    }
    const total = sources.reduce((acc, s) => acc + s.duration_us, 0);
    const list = h('div', { class: 'list' });
    sources.forEach((src, index) => {
      list.appendChild(h('div', { class: 'list-row' },
        h('span', { class: 'faint mono' }, String(index + 1).padStart(2, '0')),
        h('span', { class: 'grow' },
          h('div', { class: 'name' }, src.name),
          h('div', { class: 'meta' }, `${src.width}x${src.height} · ${src.fps}fps · ${mmss(src.duration_us)} · 오디오 ${src.audio_tracks}개`)),
        h('button', { class: 'btn sm', disabled: index === 0, onclick: () => { const t = sources[index - 1]; sources[index - 1] = sources[index]; sources[index] = t; renderList(); } }, '↑'),
        h('button', { class: 'btn sm', disabled: index === sources.length - 1, onclick: () => { const t = sources[index + 1]; sources[index + 1] = sources[index]; sources[index] = t; renderList(); } }, '↓'),
        h('button', { class: 'btn sm danger', onclick: () => { sources.splice(index, 1); renderList(); } }, '✕')));
    });
    listBox.appendChild(list);
    listBox.appendChild(h('p', { class: 'muted', style: 'margin-top:var(--sp-3)' },
      `총 ${sources.length}개 · 합계 ${mmss(total)}`));
    if (S.session.warnings && S.session.warnings.length) {
      listBox.appendChild(banner('warn', '섞인 소재', S.session.warnings));
    }
  }
  renderList();

  card.appendChild(h('h3', {}, '원본 영상 고르기'));
  card.appendChild(pathPicker({
    kind: 'video', multi: true, label: '여러 개를 한 번에 고를 수 있습니다',
    onPick: async (paths) => {
      const existing = new Set(sources.map((s) => s.path));
      const merged = sources.map((s) => s.path).concat(paths.filter((p) => !existing.has(p)));
      await saveSources(merged);
    },
  }));
  card.appendChild(listBox);
  card.appendChild(h('div', { class: 'btn-row', style: 'margin-top:var(--sp-4)' },
    h('button', {
      class: 'btn primary', disabled: !sources.length,
      onclick: () => saveSources(sources.map((s) => s.path)),
    }, '순서 저장하고 0차 완료'),
    h('button', { class: 'btn', onclick: () => saveSources([]) }, '전체 비우기')));

  async function saveSources(paths) {
    try {
      const res = await api(`/api/sessions/${S.sessionId}/sources`, { body: { paths } });
      S.session = res.session; S.steps = res.steps;
      if (res.problems && res.problems.length) {
        toast(res.problems[0].message, 'err');
      } else if (paths.length) {
        toast(`영상 ${paths.length}개를 등록했습니다. 캔버스 ${res.canvas.width}x${res.canvas.height} / ${res.canvas.fps}fps`, 'ok');
      }
      render();
    } catch (e) { toast(e.message, 'err'); }
  }

  view.innerHTML = '';
  view.appendChild(card);
  view.appendChild(backupCard());
}

function backupCard() {
  const card = h('div', { class: 'card' });
  card.appendChild(h('h3', {}, '백업과 되돌리기'));
  card.appendChild(h('p', { class: 'muted' }, '단계를 실행하기 전마다 드래프트 폴더 전체를 자동으로 복사해 둡니다. 아래에서 특정 시점으로 되돌릴 수 있습니다.'));
  const list = h('div', {});
  card.appendChild(list);
  card.appendChild(h('div', { class: 'btn-row' },
    h('button', {
      class: 'btn sm', disabled: !S.session.draft_path,
      onclick: async () => {
        try { await api(`/api/sessions/${S.sessionId}/backup`, { body: { tag: '수동' } }); toast('백업했습니다.', 'ok'); loadBackups(); }
        catch (e) { toast(e.message, 'err'); }
      }
    }, '지금 백업'),
    h('button', { class: 'btn sm', onclick: loadBackups }, '목록 새로고침')));

  async function loadBackups() {
    list.innerHTML = '<p class="muted">불러오는 중…</p>';
    try {
      const res = await api(`/api/sessions/${S.sessionId}/backups`);
      list.innerHTML = '';
      if (!res.backups.length) { list.appendChild(h('p', { class: 'muted' }, '백업이 아직 없습니다.')); return; }
      const box = h('div', { class: 'list' });
      for (const b of res.backups.slice(0, 12)) {
        box.appendChild(h('div', { class: 'list-row' },
          h('span', { class: 'grow' }, h('div', { class: 'name' }, b.name), h('div', { class: 'meta' }, b.created)),
          h('button', {
            class: 'btn sm', onclick: async () => {
              if (!confirm(`'${b.name}' 시점으로 되돌립니다. 지금 상태는 따로 백업해 둡니다. 계속할까요?`)) return;
              try {
                await api(`/api/sessions/${S.sessionId}/restore`, { body: { backup_path: b.path } });
                toast('되돌렸습니다.', 'ok');
              } catch (e) { toast(e.message, 'err'); }
            }
          }, '이 시점으로 되돌리기')));
      }
      list.appendChild(box);
    } catch (e) { list.innerHTML = ''; list.appendChild(banner('danger', null, [e.message])); }
  }
  if (S.session.draft_path) loadBackups(); else list.appendChild(h('p', { class: 'muted' }, '드래프트를 만들면 백업 목록이 표시됩니다.'));

  // 캡컷 프로젝트 목록(root_meta_info.json) 백업 — 드래프트 폴더 밖에 있어 따로 다룹니다.
  const registryBox = h('div', {});
  card.appendChild(h('h3', {}, '캡컷 프로젝트 목록 백업'));
  card.appendChild(registryBox);
  api('/api/registry-backups').then((res) => {
    registryBox.innerHTML = '';
    registryBox.appendChild(h('p', { class: 'muted' }, res.note));
    if (!res.backups.length) {
      registryBox.appendChild(h('p', { class: 'faint' }, '아직 백업이 없습니다.'));
      return;
    }
    const box = h('div', { class: 'list' });
    for (const b of res.backups.slice(0, 8)) {
      box.appendChild(h('div', { class: 'list-row' },
        h('span', { class: 'grow' },
          h('div', { class: 'name' }, b.created),
          h('div', { class: 'meta' }, `${b.size_bytes.toLocaleString()} 바이트`)),
        h('button', {
          class: 'btn sm', onclick: async () => {
            if (!confirm('캡컷 프로젝트 목록을 이 시점으로 되돌립니다. 캡컷을 완전히 종료한 상태여야 합니다. 계속할까요?')) return;
            try {
              await api('/api/registry-restore', { body: { backup_path: b.path } });
              toast('프로젝트 목록을 되돌렸습니다. 캡컷을 다시 열어 확인해 주세요.', 'ok');
            } catch (e) { toast(e.message, 'err'); }
          }
        }, '되돌리기')));
    }
    registryBox.appendChild(box);
  }).catch(() => {
    registryBox.innerHTML = '';
    registryBox.appendChild(h('p', { class: 'faint' }, '백업 목록을 읽지 못했습니다.'));
  });

  return card;
}

/* ── 자막 스타일 캘리브레이션 ────────────────────────────────────────── */
async function viewCalibration() {
  const view = document.getElementById('view');
  view.innerHTML = '';
  const card = h('div', { class: 'card' });
  card.appendChild(h('h2', {}, '자막 스타일 캘리브레이션'));
  card.appendChild(h('p', {}, '캡컷에서 직접 만든 자막의 모양을 그대로 가져옵니다. 이걸 해 두어야 2차 자막이 캡컷 화면에 제대로 보입니다.'));
  card.appendChild(banner('info', '왜 이 단계가 먼저인가', [
    'pyCapCut이 만드는 텍스트 소재는 캡컷 원본보다 필드가 91개 적습니다. 빈 껍데기에 스타일만 얹으면 자막이 화면에 나타나지 않거나 배경이 빠집니다.',
    '그래서 캡컷 원본 소재를 통째로 저장해 두고, 거기서 글자만 갈아 끼우는 방식을 씁니다.',
  ]));
  view.appendChild(card);

  let drafts = [];
  try {
    const res = await api('/api/calibration/drafts');
    drafts = res.drafts;
    card.appendChild(h('p', { class: 'faint mono' }, res.root));
  } catch (e) {
    card.appendChild(banner('danger', '드래프트 폴더를 찾지 못했습니다', [e.message]));
  }

  const summaryBox = h('div', {});
  const profileRes = await api('/api/calibration/profile').catch(() => ({ profile: null }));
  S.profile = profileRes.profile;

  function renderSummary(profile, warnings, estimated) {
    summaryBox.innerHTML = '';
    if (!profile) {
      summaryBox.appendChild(banner('warn', '아직 캘리브레이션하지 않았습니다', [profileRes.notice || '']));
      return;
    }
    const sum = profile.__summary || null;
    const f = profile.font || {}, t = profile.text || {}, b = profile.background || {}, p = profile.position || {};
    const flag = profile.check_flag || 0;
    if (estimated || profile.manual) {
      summaryBox.appendChild(banner('warn', '이 값은 추정값입니다',
        ['캡컷에서 자막이 실제로 어떻게 보이는지 반드시 눈으로 확인해 주세요. 참조 드래프트로 가져오는 쪽이 훨씬 정확합니다.']));
    }
    if (warnings && warnings.length) summaryBox.appendChild(banner('warn', '확인이 필요합니다', warnings));

    summaryBox.appendChild(h('dl', { class: 'kv' },
      h('dt', {}, '가져온 드래프트'), h('dd', {}, profile.source_draft_name || '-'),
      h('dt', {}, '캘리브레이션 시각'), h('dd', {}, profile.calibrated_at || '-'),
      h('dt', {}, '글꼴'), h('dd', {}, `${f.family || '?'} ${f.style || ''}`),
      h('dt', {}, '글꼴 파일 경로'), h('dd', {}, f.resolved_path || f.path || '(없음)'),
      h('dt', {}, '글자 크기 / 색'), h('dd', {}, `${t.size} / ${t.color_hex}`),
      h('dt', {}, '배경 색 / 스타일'), h('dd', {}, `${b.color} / ${b.style}`),
      h('dt', {}, 'check_flag'), h('dd', {},
        `${flag}  (기본 ${flag & 7 ? 'O' : 'X'} · 테두리 ${flag & 8 ? 'O' : 'X'} · 배경 ${flag & 16 ? 'O' : 'X'})`),
      h('dt', {}, '자막 Y 위치'), h('dd', {}, `${(p.y ?? 0).toFixed(4)} (정규화) = ${p.y_px}px`),
      h('dt', {}, '캔버스'), h('dd', {}, `${(profile.canvas || {}).width}x${(profile.canvas || {}).height}`),
      h('dt', {}, '저장한 원본 필드 수'), h('dd', {}, `소재 ${profile.raw_material_field_count}개 / 세그먼트 ${profile.raw_segment_field_count}개`),
    ));

    if ((flag & 16) === 0) {
      summaryBox.appendChild(banner('danger', '배경이 표시되지 않습니다',
        ['check_flag에 배경 비트(+16)가 없습니다. background_color를 넣어도 무시됩니다.']));
    }
    if ((b.style || 0) < 1) {
      summaryBox.appendChild(banner('danger', '배경 스타일이 0입니다', ['background_style은 1 이상이어야 배경이 나옵니다.']));
    }

    const transitions = profile.transitions || [];
    summaryBox.appendChild(h('h3', {}, '가져온 트랜지션'));
    if (!transitions.length) {
      summaryBox.appendChild(h('p', { class: 'muted' }, '가져온 트랜지션이 없습니다. 캡컷에서 원하는 트랜지션을 클립 사이에 한 번 적용해 저장한 뒤 다시 가져와 주세요.'));
    } else {
      const table = h('table', {}, h('thead', {}, h('tr', {},
        h('th', {}, '드래프트 이름'), h('th', {}, 'effect_id'), h('th', {}, 'pycapcut 매칭'), h('th', {}, '기본 길이'))),
        h('tbody', {}, transitions.map((tr) => h('tr', {},
          h('td', {}, tr.draft_name || '(이름 없음)'),
          h('td', { class: 'mono' }, tr.effect_id),
          h('td', {}, tr.matched
            ? h('span', { class: 'badge ok' }, tr.enum_name)
            : h('span', { class: 'badge dim' }, '찾지 못함')),
          h('td', { class: 'mono' }, secs(tr.duration_us))))));
      summaryBox.appendChild(h('div', { class: 'table-wrap' }, table));
      summaryBox.appendChild(h('p', { class: 'faint' },
        '트랜지션 이름은 중국어입니다(전체 1137개). 한국어 UI 이름과 매칭되지 않기 때문에 effect_id로 역조회합니다.'));
    }
  }
  renderSummary(S.profile, [], false);

  const pickerCard = h('div', { class: 'card' });
  pickerCard.appendChild(h('h3', {}, '참조 드래프트에서 가져오기'));
  const select = h('select', {}, h('option', { value: '' }, '— 드래프트 선택 —'),
    ...drafts.map((d) => h('option', { value: d.path }, `${d.name}  (${d.modified})`)));
  pickerCard.appendChild(select);
  pickerCard.appendChild(h('div', { class: 'btn-row', style: 'margin-top:var(--sp-3)' },
    h('button', {
      class: 'btn primary', onclick: async () => {
        if (!select.value) { toast('드래프트를 골라 주세요.'); return; }
        try {
          const res = await api('/api/calibration/from-draft', { body: { draft_path: select.value } });
          S.profile = res.profile;
          renderSummary(res.profile, res.warnings, false);
          toast(`자막 스타일과 트랜지션 ${res.transitions.length}개를 가져왔습니다.`, 'ok');
          await refreshSession();
        } catch (e) { toast(e.message, 'err'); }
      }
    }, '자막 스타일 + 트랜지션 가져오기'),
    h('button', {
      class: 'btn', onclick: async () => {
        if (!select.value) { toast('드래프트를 골라 주세요.'); return; }
        try {
          const res = await api('/api/calibration/transitions/from-draft', { body: { draft_path: select.value } });
          if (res.notice) toast(res.notice, 'err');
          else toast(`트랜지션 ${res.transitions.length}개를 가져왔습니다.`, 'ok');
          const p = await api('/api/calibration/profile');
          S.profile = p.profile; renderSummary(p.profile, [], false);
        } catch (e) { toast(e.message, 'err'); }
      }
    }, '트랜지션만 다시 가져오기')));
  view.appendChild(pickerCard);

  const manualCard = h('div', { class: 'card' });
  manualCard.appendChild(h('h3', {}, '수동 입력 (폴백)'));
  manualCard.appendChild(h('p', { class: 'muted' }, '참조 드래프트가 없을 때만 쓰세요. 아래 값은 추정값으로 저장되며, 캡컷에서 눈으로 확인해야 합니다.'));
  const mf = {
    font_family: h('input', { type: 'text', value: 'Pretendard' }),
    font_style: h('input', { type: 'text', value: 'Bold' }),
    font_size: h('input', { type: 'number', step: '0.5', value: '5' }),
    text_color: h('input', { type: 'text', value: '#000000' }),
    background_color: h('input', { type: 'text', value: '#ffffff' }),
    canvas_height: h('input', { type: 'number', value: '1080' }),
    position_y_px: h('input', { type: 'number', value: '-394' }),
  };
  manualCard.appendChild(h('div', { class: 'grid-3' },
    h('label', { class: 'field' }, h('span', {}, '글꼴 계열'), mf.font_family),
    h('label', { class: 'field' }, h('span', {}, '굵기'), mf.font_style),
    h('label', { class: 'field' }, h('span', {}, '글자 크기'), mf.font_size),
    h('label', { class: 'field' }, h('span', {}, '글자 색'), mf.text_color),
    h('label', { class: 'field' }, h('span', {}, '배경 색'), mf.background_color),
    h('label', { class: 'field' }, h('span', {}, '캔버스 높이(px)'), mf.canvas_height),
    h('label', { class: 'field' }, h('span', {}, '자막 Y (px, 아래가 음수)'), mf.position_y_px)));
  manualCard.appendChild(banner('warn', '픽셀 값은 캔버스 높이에 따라 달라집니다', [
    '좌표는 캔버스 절반 높이를 1로 보는 정규화 값으로 저장됩니다. 4K 기준 -788px을 1080 캔버스에 그대로 넣으면 -1.459가 되어 화면 밖으로 나갑니다. 1080에서 같은 위치는 -394px입니다.',
  ]));
  manualCard.appendChild(h('div', { class: 'btn-row' },
    h('button', {
      class: 'btn', onclick: async () => {
        const body = {};
        for (const [key, node] of Object.entries(mf)) body[key] = node.type === 'number' ? Number(node.value) : node.value;
        try {
          const res = await api('/api/calibration/manual', { body });
          S.profile = res.profile;
          renderSummary(res.profile, res.warnings, true);
          toast('수동 값으로 저장했습니다. 추정값이라는 점을 기억해 주세요.', 'ok');
          await refreshSession();
        } catch (e) { toast(e.message, 'err'); }
      }
    }, '수동 값으로 저장')));
  view.appendChild(manualCard);

  const summaryCard = h('div', { class: 'card' });
  summaryCard.appendChild(h('h3', {}, '현재 저장된 스타일'));
  summaryCard.appendChild(summaryBox);
  view.appendChild(summaryCard);
}

/* ── 1차 컷 편집 ─────────────────────────────────────────────────────── */
async function viewStage1() {
  const view = document.getElementById('view');
  view.innerHTML = '';

  const card = h('div', { class: 'card' });
  card.appendChild(h('h2', {}, '1차 컷 편집'));
  card.appendChild(h('p', {}, '무음·필러·반복을 찾아 후보로 올립니다. 자동으로 잘라내지 않습니다. 아래 목록에서 직접 검수해 주세요.'));
  view.appendChild(card);

  const settings = S.session.cut_settings || {};
  const sliders = {};
  function slider(key, label, min, max, step, unit, note) {
    const input = h('input', { type: 'range', min, max, step, value: settings[key] });
    const out = h('b', {}, `${settings[key]}${unit}`);
    input.addEventListener('input', () => { out.textContent = `${input.value}${unit}`; });
    sliders[key] = input;
    return h('div', { class: 'slider-row' },
      h('div', { class: 'slider-head' }, h('span', {}, label), out),
      input,
      note ? h('div', { class: 'faint' }, note) : null);
  }
  card.appendChild(h('h3', {}, '감지 설정'));
  card.appendChild(h('div', { class: 'grid-2' },
    h('div', {},
      slider('threshold_db', '무음 임계값', -60, -10, 1, 'dB', '값을 올리면 더 조용한 구간까지 무음으로 봅니다.'),
      slider('min_duration', '최소 무음 길이', 0.2, 3, 0.05, '초', '공격적으로 잡으면 문장 사이 자연스러운 호흡까지 잘려 부자연스러워집니다.')),
    h('div', {},
      slider('tail_pad', '말 끝난 뒤 여유', 0, 1.2, 0.05, '초', '⚠️ 짧으면 마지막 음절이 잘려 들립니다. 넉넉히 두세요 (기본 0.35초).'),
      slider('head_pad', '다음 말 시작 전 여유', 0, 1.0, 0.05, '초', '다음 말이 시작되기 전 남길 여유입니다 (기본 0.15초).'))));
  card.appendChild(banner('info', '앞뒤 여유는 왜 따로인가', [
    '무음 구간의 시작은 "말이 끝난 직후"이고 끝은 "다음 말 시작 직전"입니다. silencedetect는 소리가 임계값 아래로 떨어지는 순간을 무음 시작으로 보는데, 말끝의 자음과 여운은 이미 그 아래라서 뒤쪽 여유가 짧으면 마지막 음절이 잘려 들립니다.',
  ]));

  const progress = progressBox();
  card.appendChild(h('div', { class: 'btn-row' },
    h('button', {
      class: 'btn primary', onclick: async () => {
        const payload = {};
        for (const [key, node] of Object.entries(sliders)) payload[key] = Number(node.value);
        try {
          const job = await api(`/api/editing/${S.sessionId}/analyze`, { body: { settings: payload } });
          runJob(job, progress, async () => { toast('분석이 끝났습니다.', 'ok'); await loadCuts(); });
        } catch (e) { toast(e.message, 'err'); }
      }
    }, '분석 실행 (오디오 · 무음 · 음성 인식)'),
    h('button', {
      class: 'btn', onclick: async () => {
        const payload = {};
        for (const [key, node] of Object.entries(sliders)) payload[key] = Number(node.value);
        try {
          const res = await api(`/api/editing/${S.sessionId}/cuts/recalc`, { body: { settings: payload } });
          if (res.needs_reanalyze) { toast(res.message, 'err'); return; }
          S.cuts = res; toast('여유 값만 반영해 다시 계산했습니다.', 'ok'); renderCuts();
        } catch (e) { toast(e.message, 'err'); }
      }
    }, '여유 값만 다시 계산'),
    h('button', {
      class: 'btn sm', onclick: async () => {
        try {
          const job = await api(`/api/editing/${S.sessionId}/analyze`, { body: { force: true } });
          runJob(job, progress, async () => { toast('처음부터 다시 분석했습니다.', 'ok'); await loadCuts(); });
        } catch (e) { toast(e.message, 'err'); }
      }
    }, '캐시 무시하고 처음부터')));
  card.appendChild(progress);

  const cutsCard = h('div', { class: 'card' });
  view.appendChild(cutsCard);

  async function loadCuts() {
    try { S.cuts = await api(`/api/editing/${S.sessionId}/cuts`); } catch (e) { toast(e.message, 'err'); return; }
    renderCuts();
    await refreshSession(false);
  }

  function renderCuts() {
    cutsCard.innerHTML = '';
    cutsCard.appendChild(h('h3', {}, '컷 후보 검수'));
    if (!S.cuts || !S.cuts.candidates.length) {
      cutsCard.appendChild(h('p', { class: 'muted' }, '아직 후보가 없습니다. 위에서 분석을 실행해 주세요.'));
      return;
    }
    const sum = S.cuts.summary;
    cutsCard.appendChild(h('dl', { class: 'kv' },
      h('dt', {}, '원본 길이'), h('dd', {}, sum.total_label),
      h('dt', {}, '남는 길이'), h('dd', {}, `${sum.kept_label}  (${sum.span_count}구간)`),
      h('dt', {}, '잘리는 비율'), h('dd', {}, `${(sum.removed_ratio * 100).toFixed(1)}%`)));
    if (sum.warnings.length) cutsCard.appendChild(banner('warn', '확인이 필요합니다', sum.warnings));

    const kindRow = h('div', { class: 'btn-row', style: 'margin:var(--sp-3) 0' });
    for (const [kind, label] of [['silence', '무음'], ['filler', '필러'], ['repeat', '반복']]) {
      const info = sum.by_kind[kind];
      if (!info) continue;
      kindRow.appendChild(h('span', { class: 'badge ' + kind }, `${label} ${info.selected}/${info.count}`));
      kindRow.appendChild(h('button', { class: 'btn sm', onclick: () => toggleKind(kind, true) }, '전체 선택'));
      kindRow.appendChild(h('button', { class: 'btn sm', onclick: () => toggleKind(kind, false) }, '전체 해제'));
    }
    kindRow.appendChild(h('button', {
      class: 'btn sm', onclick: async () => {
        try {
          const res = await api(`/api/editing/${S.sessionId}/cuts/keep-long-silence`, { body: { threshold_sec: 3 } });
          S.cuts = Object.assign({}, S.cuts, res); toast(res.message, 'ok'); renderCuts();
        } catch (e) { toast(e.message, 'err'); }
      }
    }, '긴 무음(3초 이상) 살리기'));
    cutsCard.appendChild(kindRow);

    const rows = S.cuts.candidates.map((c) => {
      const check = h('input', { type: 'checkbox', checked: c.selected });
      check.addEventListener('change', () => applySelection(c.id, check.checked, tr));
      const tr = h('tr', { class: c.selected ? '' : 'deselected' },
        h('td', {}, check),
        h('td', { class: 'mono' }, tc(c.start_us), h('br'), h('span', { class: 'faint' }, tc(c.end_us))),
        h('td', {}, h('span', { class: 'badge ' + c.kind }, c.kind_label)),
        h('td', { class: 'mono' }, secs(c.duration_us)),
        h('td', {},
          h('div', { class: 'faint' }, c.context_before || ''),
          h('div', {}, c.label),
          h('div', { class: 'faint' }, c.context_after || '')),
        h('td', {}, h('button', {
          class: 'btn sm', onclick: (ev) => {
            const audio = new Audio(`/api/editing/${S.sessionId}/preview?start_us=${c.start_us}&end_us=${c.end_us}`);
            audio.play().catch(() => toast('미리듣기를 재생하지 못했습니다.', 'err'));
            ev.currentTarget.textContent = '▶ 재생 중';
            audio.onended = () => { ev.currentTarget.textContent = '미리듣기'; };
          }
        }, '미리듣기')));
      return tr;
    });

    cutsCard.appendChild(h('div', { class: 'table-wrap' },
      h('table', {},
        h('thead', {}, h('tr', {},
          h('th', {}, '자를까요'), h('th', {}, '타임코드'), h('th', {}, '유형'),
          h('th', {}, '길이'), h('th', {}, '앞뒤 문맥'), h('th', {}, ''))),
        h('tbody', {}, rows))));

    const draftName = h('input', { type: 'text', value: S.session.draft_name || `자동편집_${S.sessionId}` });
    const buildProgress = progressBox();
    cutsCard.appendChild(h('h3', {}, '드래프트 만들기'));
    cutsCard.appendChild(h('label', { class: 'field' }, h('span', {}, '캡컷 드래프트 이름'), draftName));
    cutsCard.appendChild(h('div', { class: 'btn-row' },
      h('button', {
        class: 'btn primary', onclick: async () => {
          try {
            const job = await api(`/api/editing/${S.sessionId}/build-draft`, { body: { draft_name: draftName.value } });
            runJob(job, buildProgress, async (payload) => {
              toast(payload.label, 'ok');
              showVerified(payload.result.verified);
              await refreshSession();
            });
          } catch (e) { toast(e.message, 'err'); }
        }
      }, '컷 적용해 드래프트 만들기')));
    cutsCard.appendChild(buildProgress);
  }

  async function applySelection(id, value, tr) {
    try {
      const res = await api(`/api/editing/${S.sessionId}/cuts`, { method: 'PATCH', body: { selections: { [id]: value } } });
      S.cuts = Object.assign({}, S.cuts, res);
      tr.classList.toggle('deselected', !value);
    } catch (e) { toast(e.message, 'err'); }
  }
  async function toggleKind(kind, value) {
    try {
      const res = await api(`/api/editing/${S.sessionId}/cuts`, { method: 'PATCH', body: { kind, value } });
      S.cuts = Object.assign({}, S.cuts, res); renderCuts();
    } catch (e) { toast(e.message, 'err'); }
  }

  await loadCuts();
}

function showVerified(verified) {
  if (!verified) return;
  const body = [
    h('dl', { class: 'kv' },
      h('dt', {}, '드래프트'), h('dd', {}, verified.draft),
      h('dt', {}, '캔버스'), h('dd', {}, `${verified.canvas.width}x${verified.canvas.height} · ratio "${verified.canvas.ratio}"`),
      h('dt', {}, '길이'), h('dd', {}, tc(verified.duration_us)),
      h('dt', {}, '자막 세그먼트'), h('dd', {}, String(verified.text_segment_count)),
      h('dt', {}, '트랜지션'), h('dd', {}, String(verified.transition_count)),
      h('dt', {}, '이미지 소재'), h('dd', {}, String(verified.photo_material_count))),
    h('h3', {}, '트랙별 레이어 확인'),
    h('div', { class: 'table-wrap' }, h('table', {},
      h('thead', {}, h('tr', {}, h('th', {}, '#'), h('th', {}, '종류'), h('th', {}, '이름'), h('th', {}, '세그먼트'), h('th', {}, 'track_render_index'))),
      h('tbody', {}, verified.tracks.map((t) => h('tr', {},
        h('td', {}, String(t.index)), h('td', {}, t.type), h('td', {}, t.name || '-'),
        h('td', {}, String(t.segment_count)),
        h('td', {}, t.layered_correctly
          ? h('span', { class: 'badge ok' }, JSON.stringify(t.track_render_index))
          : h('span', { class: 'badge repeat' }, JSON.stringify(t.track_render_index)))))))),
  ];
  if (verified.problems && verified.problems.length) body.unshift(banner('danger', '문제가 남아 있습니다', verified.problems));
  else body.unshift(banner('ok', '파일을 다시 읽어 확인했습니다', ['레이어와 캔버스 비율이 올바릅니다.']));
  modal('드래프트 검증 결과', body);
}

/* ── 2차 자막 생성 ───────────────────────────────────────────────────── */
async function viewStage2() {
  const view = document.getElementById('view');
  view.innerHTML = '';

  const card = h('div', { class: 'card' });
  card.appendChild(h('h2', {}, '2차 자막 생성'));
  card.appendChild(h('p', {}, '음성 인식 결과로 자막을 만듭니다. 대본이 있으면 대본을 정본으로 삼고 타임코드만 음성 인식에서 가져옵니다.'));
  view.appendChild(card);

  // 컷 서명 사전 경고 (요청서 3.18)
  try {
    const check = await api(`/api/editing/${S.sessionId}/signature-check`);
    if (!check.ok && check.kind === 'mismatch') {
      card.appendChild(banner('danger', '드래프트와 컷 설정이 어긋납니다', [check.message]));
    } else if (check.ok) {
      card.appendChild(banner('ok', '드래프트와 컷 설정이 일치합니다', [
        `남는 길이 ${tc(check.signature.kept_duration_us)} · ${check.signature.span_count}구간`,
      ]));
    } else if (check.kind !== 'no_draft') {
      card.appendChild(banner('warn', null, [check.message]));
    }
  } catch (e) { /* 조용히 넘기지 않고 아래에서 다시 알립니다 */ card.appendChild(banner('warn', null, [e.message])); }

  let scriptPath = S.session.script_path || '';
  const scriptLabel = h('div', { class: 'mono faint' }, scriptPath || '(대본 없음 — 음성 인식 결과만 사용)');
  card.appendChild(h('h3', {}, '대본 (선택)'));
  card.appendChild(pathPicker({
    kind: 'script', multi: false, label: 'txt / srt / md',
    onPick: (paths) => { scriptPath = paths[0]; scriptLabel.textContent = scriptPath; },
  }));
  card.appendChild(scriptLabel);

  const maxChars = h('input', { type: 'number', min: 12, max: 60, value: (S.session.subtitle_settings || {}).max_chars || 36 });
  card.appendChild(h('label', { class: 'field', style: 'max-width:260px;margin-top:var(--sp-4)' },
    h('span', {}, '한 줄 최대 글자 수'), maxChars));
  card.appendChild(banner('info', '자막 줄바꿈 규칙', [
    '자막은 항상 한 줄입니다. 문장 끝(. ! ? …)과 구절(, · ; :)에서 끊고 부호는 앞 조각에 붙입니다.',
    '상한을 넘는 구절만 어절 단위로 더 쪼개되, 앞에서부터 꽉 채우지 않고 필요한 조각 수를 먼저 계산해 고르게 나눕니다.',
    '한글은 어절 단위로만, 영문도 하이픈 없이 단어 단위로만 끊습니다.',
  ]));

  const tableBox = h('div', {});
  card.appendChild(h('div', { class: 'btn-row' },
    h('button', {
      class: 'btn primary', onclick: async () => {
        try {
          const res = await api(`/api/editing/${S.sessionId}/subtitles/generate`, {
            body: { script_path: scriptPath, max_chars: Number(maxChars.value) },
          });
          S.subtitles = res.subtitles;
          toast(`자막 ${res.count}개를 만들었습니다. ${res.script_notice}`, 'ok');
          renderTable(res);
          await refreshSession(false);
        } catch (e) { toast(e.message, 'err'); }
      }
    }, '자막 만들기'),
    h('button', {
      class: 'btn', onclick: () => {
        window.open(`/api/editing/${S.sessionId}/subtitles/srt`, '_blank');
      }
    }, 'SRT로 내보내기'),
    h('button', { class: 'btn sm', onclick: () => openGlossary() }, '용어 사전 편집')));

  const subsCard = h('div', { class: 'card' });
  subsCard.appendChild(h('h3', {}, '자막 검수'));
  subsCard.appendChild(tableBox);
  view.appendChild(subsCard);

  const applyCard = h('div', { class: 'card' });
  const applyProgress = progressBox();
  applyCard.appendChild(h('h3', {}, '드래프트에 넣기'));
  applyCard.appendChild(h('p', { class: 'muted' }, '넣기 직전에 드래프트의 컷 서명과 지금 컷 설정을 대조합니다. 다르면 넣지 않고 막습니다.'));
  applyCard.appendChild(h('div', { class: 'btn-row' },
    h('button', {
      class: 'btn primary', onclick: async () => {
        try {
          const job = await api(`/api/editing/${S.sessionId}/subtitles/apply`, { body: {} });
          runJob(job, applyProgress, async (payload) => {
            toast(payload.label, 'ok');
            showVerified(payload.result.report.details.verified);
            await refreshSession();
          });
        } catch (e) { toast(e.message, 'err'); }
      }
    }, '자막을 드래프트에 넣기')));
  applyCard.appendChild(applyProgress);
  view.appendChild(applyCard);

  function renderTable(meta) {
    tableBox.innerHTML = '';
    if (!S.subtitles.length) { tableBox.appendChild(h('p', { class: 'muted' }, '아직 자막이 없습니다.')); return; }
    if (meta && meta.warnings && meta.warnings.length) tableBox.appendChild(banner('warn', null, meta.warnings));

    const limit = Number(maxChars.value);
    const rows = S.subtitles.map((sub) => {
      const input = h('input', { type: 'text', value: sub.text });
      const lenBadge = h('span', { class: 'faint mono' }, `${sub.text.length}자`);
      input.addEventListener('change', async () => {
        try {
          const res = await api(`/api/editing/${S.sessionId}/subtitles`, {
            method: 'PATCH', body: { index: sub.index, text: input.value },
          });
          S.subtitles = res.subtitles; lenBadge.textContent = `${input.value.length}자`;
          toast('수정했습니다.', 'ok');
        } catch (e) { toast(e.message, 'err'); }
      });
      const flags = [];
      if (sub.source === 'script') flags.push(h('span', { class: 'badge ok' }, '대본'));
      else if (sub.source === 'mixed') flags.push(h('span', { class: 'badge dim' }, '수정'));
      else flags.push(h('span', { class: 'badge silence' }, 'STT'));
      if (sub.source === 'stt' && sub.confidence < 0.6) flags.push(h('span', { class: 'badge filler' }, '신뢰도 낮음'));
      if (sub.text.length > limit) flags.push(h('span', { class: 'badge repeat' }, '길이 초과'));

      return h('tr', {},
        h('td', { class: 'mono' }, String(sub.index)),
        h('td', { class: 'mono' }, tc(sub.start_us), h('br'), h('span', { class: 'faint' }, tc(sub.end_us))),
        h('td', {}, input),
        h('td', {}, lenBadge),
        h('td', {}, flags));
    });
    tableBox.appendChild(h('div', { class: 'table-wrap' }, h('table', {},
      h('thead', {}, h('tr', {}, h('th', {}, '#'), h('th', {}, '타임코드'), h('th', {}, '내용'), h('th', {}, '길이'), h('th', {}, '표시'))),
      h('tbody', {}, rows))));
    tableBox.appendChild(h('p', { class: 'faint' }, `총 ${S.subtitles.length}개 · 내용을 고치면 자동 저장됩니다.`));
  }

  try {
    const res = await api(`/api/editing/${S.sessionId}/subtitles`);
    S.subtitles = res.subtitles;
  } catch (_) { S.subtitles = []; }
  renderTable(null);
}

async function openGlossary() {
  let data;
  try { data = await api('/api/setup/glossary'); } catch (e) { toast(e.message, 'err'); return; }
  const boxes = [];
  const body = [h('p', { class: 'muted' }, '체크한 용어는 자막에서 영어 원문 표기로 유지됩니다. 한글 음차(예: "카고와이즈")도 영어로 되돌립니다.')];
  for (const [group, entries] of Object.entries(data)) {
    body.push(h('h3', {}, group));
    const grid = h('div', { class: 'grid-3' });
    for (const entry of entries) {
      const cb = h('input', { type: 'checkbox', checked: entry.enabled !== false });
      boxes.push({ group, term: entry.term, cb });
      grid.appendChild(h('label', { style: 'display:flex;gap:var(--sp-2);align-items:center' }, cb, entry.term));
    }
    body.push(grid);
  }
  modal('용어 사전', body, [h('button', {
    class: 'btn primary', onclick: async () => {
      const payload = {};
      for (const item of boxes) {
        payload[item.group] = payload[item.group] || [];
        payload[item.group].push({ term: item.term, enabled: item.cb.checked });
      }
      try { await api('/api/setup/glossary', { body: payload }); toast('사전을 저장했습니다.', 'ok'); closeModal(); }
      catch (e) { toast(e.message, 'err'); }
    }
  }, '저장')]);
}

/* ── 3차 트랜지션·효과음 ─────────────────────────────────────────────── */
async function viewStage3() {
  const view = document.getElementById('view');
  view.innerHTML = '';

  const card = h('div', { class: 'card' });
  card.appendChild(h('h2', {}, '3차 트랜지션 및 효과음'));
  card.appendChild(h('p', {}, '드래프트의 이미지 클립을 찾아 앞뒤에 트랜지션을 붙이고, 효과음을 얹습니다.'));
  card.appendChild(banner('info', '트랜지션은 앞쪽 세그먼트에 붙습니다', [
    'pyCapCut 규칙입니다. 아래 목록에서 "이 클립 뒤"를 고르면 해당 클립에 트랜지션이 붙습니다.',
    '마지막 세그먼트에는 붙일 수 없습니다.',
  ]));
  view.appendChild(card);

  let data;
  try { data = await api(`/api/editing/${S.sessionId}/image-clips`); }
  catch (e) { card.appendChild(banner('danger', null, [e.message])); return; }

  const allRes = await api('/api/calibration/transitions/all').catch(() => ({ transitions: [], count: 0 }));
  const calibrated = data.calibrated_transitions.filter((t) => t.matched);

  if (data.notice) card.appendChild(banner('warn', null, [data.notice]));
  if (!calibrated.length) {
    card.appendChild(banner('warn', '가져온 트랜지션이 없습니다', [
      '캘리브레이션 화면에서 캡컷 드래프트의 트랜지션을 먼저 가져오면 원하는 모양을 정확히 쓸 수 있습니다. 지금은 전체 목록(중국어 이름)에서 골라야 합니다.',
    ]));
  }

  const durationInput = h('input', { type: 'range', min: 0.1, max: 3, step: 0.1, value: 0.5 });
  const durationOut = h('b', {}, '0.50초');
  durationInput.addEventListener('input', () => { durationOut.textContent = Number(durationInput.value).toFixed(2) + '초'; });
  card.appendChild(h('div', { class: 'slider-row' },
    h('div', { class: 'slider-head' }, h('span', {}, '트랜지션 길이'), durationOut), durationInput));

  function transitionSelect() {
    const opts = [h('option', { value: '' }, '— 사용 안 함 —')];
    if (calibrated.length) {
      opts.push(h('optgroup', { label: '캡컷에서 가져온 것' },
        ...calibrated.map((t) => h('option', { value: t.enum_name }, `${t.draft_name || t.enum_name}  (${t.effect_id})`))));
    }
    opts.push(h('optgroup', { label: `전체 ${allRes.count}개 (이름은 중국어)` },
      ...allRes.transitions.slice(0, 1200).map((t) => h('option', { value: t.enum_name }, t.name))));
    return h('select', {}, ...opts);
  }

  const selects = [];
  const clipRows = data.clips.map((clip) => {
    const sel = transitionSelect();
    if (calibrated.length) sel.value = calibrated[0].enum_name;
    const cb = h('input', { type: 'checkbox', checked: clip.has_next });
    cb.disabled = !clip.has_next;
    selects.push({ clip, sel, cb });
    return h('tr', {},
      h('td', {}, cb),
      h('td', {}, clip.name),
      h('td', { class: 'mono' }, `${tc(clip.start_us)} · ${secs(clip.duration_us)}`),
      h('td', {}, clip.cross_checked
        ? h('span', { class: 'badge ok' }, 'photo + 확장자 일치')
        : h('span', { class: 'badge dim' }, clip.type_photo ? 'photo (확장자 불일치)' : '확장자만 일치')),
      h('td', {}, clip.has_next ? sel : h('span', { class: 'faint' }, '마지막 클립이라 붙일 수 없습니다')));
  });

  if (clipRows.length) {
    card.appendChild(h('div', { class: 'table-wrap' }, h('table', {},
      h('thead', {}, h('tr', {}, h('th', {}, '적용'), h('th', {}, '클립'), h('th', {}, '위치'), h('th', {}, '판정'), h('th', {}, '이 클립 뒤 트랜지션'))),
      h('tbody', {}, clipRows))));
    card.appendChild(h('div', { class: 'btn-row', style: 'margin-top:var(--sp-3)' },
      h('button', {
        class: 'btn primary', onclick: async () => {
          const requests = selects.filter((s) => s.cb.checked && s.sel.value).map((s) => ({
            track_index: s.clip.track_index,
            segment_index: s.clip.segment_index,
            enum_name: s.sel.value,
            duration_us: Math.round(Number(durationInput.value) * US),
          }));
          if (!requests.length) { toast('적용할 트랜지션을 골라 주세요.'); return; }
          try {
            const res = await api(`/api/editing/${S.sessionId}/transitions/apply`, { body: { transitions: requests } });
            toast(`트랜지션 ${res.details.applied}개 적용 · 파일에서 확인된 트랜지션 ${res.details.confirmed_in_file}개`, 'ok');
            if (res.warnings.length) modal('확인이 필요합니다', [banner('warn', null, res.warnings)]);
            await refreshSession();
          } catch (e) { toast(e.message, 'err'); }
        }
      }, '트랜지션 적용')));
  }

  // ── 효과음 ─────────────────────────────────────────────────────────
  const sfxCard = h('div', { class: 'card' });
  sfxCard.appendChild(h('h3', {}, '효과음'));
  const sfx = await api('/api/files/sfx').catch(() => ({ items: [], notice: '' }));
  if (sfx.notice) sfxCard.appendChild(banner('warn', null, [sfx.notice]));
  sfxCard.appendChild(h('p', { class: 'faint mono' }, sfx.folder));

  const placements = [];
  const placementBox = h('div', {});
  function renderPlacements() {
    placementBox.innerHTML = '';
    if (!placements.length) { placementBox.appendChild(h('p', { class: 'muted' }, '얹은 효과음이 없습니다.')); return; }
    const list = h('div', { class: 'list' });
    placements.forEach((p, i) => {
      list.appendChild(h('div', { class: 'list-row' },
        h('span', { class: 'grow' }, h('div', { class: 'name' }, p.name), h('div', { class: 'meta' }, tc(p.start_us))),
        h('span', { class: 'faint mono' }, `볼륨 ${p.volume.toFixed(2)}`),
        h('button', { class: 'btn sm danger', onclick: () => { placements.splice(i, 1); renderPlacements(); } }, '✕')));
    });
    placementBox.appendChild(list);
  }
  renderPlacements();

  if (sfx.items.length) {
    const fileSel = h('select', {}, ...sfx.items.map((f) => h('option', { value: f.path }, f.name)));
    const atInput = h('input', { type: 'text', placeholder: '예: 00:01:23.400 또는 83.4', value: '00:00:00.000' });
    const volInput = h('input', { type: 'range', min: 0, max: 1.5, step: 0.05, value: 1 });
    const volOut = h('b', {}, '1.00');
    volInput.addEventListener('input', () => { volOut.textContent = Number(volInput.value).toFixed(2); });

    sfxCard.appendChild(h('div', { class: 'grid-3' },
      h('label', { class: 'field' }, h('span', {}, '효과음'), fileSel),
      h('label', { class: 'field' }, h('span', {}, '넣을 위치 (편집본 기준)'), atInput),
      h('div', { class: 'slider-row' }, h('div', { class: 'slider-head' }, h('span', {}, '볼륨'), volOut), volInput)));
    sfxCard.appendChild(h('div', { class: 'btn-row' },
      h('button', {
        class: 'btn sm', onclick: () => {
          const us = parseTime(atInput.value);
          if (us === null) { toast('위치를 00:00:00.000 또는 초 단위 숫자로 넣어 주세요.', 'err'); return; }
          const item = sfx.items.find((f) => f.path === fileSel.value);
          placements.push({ path: item.path, name: item.name, start_us: us, volume: Number(volInput.value) });
          renderPlacements();
        }
      }, '목록에 추가')));
  }
  sfxCard.appendChild(placementBox);
  sfxCard.appendChild(h('div', { class: 'btn-row', style: 'margin-top:var(--sp-3)' },
    h('button', {
      class: 'btn primary', onclick: async () => {
        try {
          const res = await api(`/api/editing/${S.sessionId}/sfx/apply`, { body: { placements } });
          toast(`효과음 ${res.details.confirmed}개를 넣었습니다.`, 'ok');
          if (res.warnings.length) modal('확인이 필요합니다', [banner('warn', null, res.warnings)]);
          await refreshSession();
        } catch (e) { toast(e.message, 'err'); }
      }
    }, '효과음 적용')));
  view.appendChild(sfxCard);
}

function parseTime(text) {
  const raw = (text || '').trim();
  if (!raw) return null;
  if (/^\d+(\.\d+)?$/.test(raw)) return Math.round(Number(raw) * US);
  const m = raw.match(/^(?:(\d+):)?(\d{1,2}):(\d{1,2})(?:[.,](\d{1,3}))?$/);
  if (!m) return null;
  const [, hh, mm, ss, ms] = m;
  return Math.round(((Number(hh || 0) * 3600 + Number(mm) * 60 + Number(ss)) * 1000 + Number((ms || '0').padEnd(3, '0'))) * 1000);
}

/* ── 4차 내보내기·마케팅 문구 ────────────────────────────────────────── */
async function viewStage4() {
  const view = document.getElementById('view');
  view.innerHTML = '';

  const card = h('div', { class: 'card' });
  card.appendChild(h('h2', {}, '4차 내보내기 및 마케팅 문구'));
  view.appendChild(card);

  let info;
  try { info = await api(`/api/publishing/${S.sessionId}/export-info`); }
  catch (e) { card.appendChild(banner('danger', null, [e.message])); return; }

  card.appendChild(h('h3', {}, '캡컷에서 직접 내보내기 (권장)'));
  card.appendChild(h('ol', { class: 'muted' }, ...info.guide.map((g) => h('li', { html: g.replace(/\*\*(.+?)\*\*/g, '<strong>$1</strong>') }))));
  card.appendChild(h('p', { class: 'faint mono' }, info.draft_path || '(드래프트 없음)'));

  const autoCard = h('div', { class: 'card' });
  autoCard.appendChild(h('h3', {}, '자동 내보내기 (미검증 · 기본 꺼짐)'));
  autoCard.appendChild(banner('danger', '이 기능만 캡컷이 실행 중이어야 합니다', info.auto_export.risks));
  const confirmCb = h('input', { type: 'checkbox' });
  const outPath = h('input', { type: 'text', placeholder: '출력 파일 경로 (비우면 캡컷 기본값)' });
  const autoProgress = progressBox();
  autoCard.appendChild(h('label', { style: 'display:flex;gap:var(--sp-2);align-items:flex-start;margin-bottom:var(--sp-3)' },
    confirmCb, h('span', { class: 'muted' }, '위 내용을 확인했고, 실패해도 캡컷에서 직접 내보내면 된다는 것을 이해했습니다.')));
  autoCard.appendChild(h('label', { class: 'field' }, h('span', {}, '출력 경로'), outPath));
  autoCard.appendChild(h('div', { class: 'btn-row' },
    h('button', {
      class: 'btn', onclick: async () => {
        if (!confirmCb.checked) { toast('확인 체크박스를 먼저 눌러 주세요.', 'err'); return; }
        try {
          const job = await api(`/api/publishing/${S.sessionId}/auto-export`, {
            body: { confirmed: true, output_path: outPath.value },
          });
          runJob(job, autoProgress, (payload) => toast(payload.label, 'ok'));
        } catch (e) { toast(e.message, 'err'); }
      }
    }, '자동 내보내기 시도')));
  autoCard.appendChild(autoProgress);
  view.appendChild(autoCard);

  const promptCard = h('div', { class: 'card' });
  promptCard.appendChild(h('h3', {}, '마케팅 문구 프롬프트'));
  promptCard.appendChild(h('p', { class: 'muted' }, '아래 3문항에 답하면 자막 전문과 합쳐 프롬프트를 조립합니다. 외부 API를 호출하지 않고, 완성된 텍스트를 복사해 쓰시면 됩니다.'));
  const q = {
    takeaway: h('textarea', { rows: 2, placeholder: '예: HBL과 MBL을 헷갈리지 않고 구분하는 기준' }),
    situation: h('textarea', { rows: 2, placeholder: '예: 포워딩 회사에 막 입사해 B/L을 처음 발행해 보는 사람' }),
    tool_name: h('textarea', { rows: 2, placeholder: '예: CargoWise, 캡컷 자동 편집기' }),
  };
  promptCard.appendChild(h('label', { class: 'field' }, h('span', {}, '1. 이번 영상에서 시청자가 가져가는 가장 큰 하나는?'), q.takeaway));
  promptCard.appendChild(h('label', { class: 'field' }, h('span', {}, '2. 어떤 상황에 놓인 사람에게 필요한가? (직무, 업무 상황)'), q.situation));
  promptCard.appendChild(h('label', { class: 'field' }, h('span', {}, '3. 다루는 도구나 기능의 정확한 이름은?'), q.tool_name));

  const promptBox = h('textarea', { class: 'prompt-box hidden', readonly: true });
  const savedLine = h('div', { class: 'faint mono' });
  promptCard.appendChild(h('div', { class: 'btn-row' },
    h('button', {
      class: 'btn primary', onclick: async () => {
        try {
          const res = await api(`/api/publishing/${S.sessionId}/marketing-prompt`, {
            body: { takeaway: q.takeaway.value, situation: q.situation.value, tool_name: q.tool_name.value },
          });
          promptBox.value = res.prompt; promptBox.classList.remove('hidden');
          savedLine.textContent = `txt 저장: ${res.saved_path}  (${res.char_count}자 · 자막 ${res.subtitle_count}개 포함)`;
          toast('프롬프트를 만들었습니다.', 'ok');
          await refreshSession();
        } catch (e) { toast(e.message, 'err'); }
      }
    }, '프롬프트 만들기'),
    h('button', { class: 'btn', onclick: () => copyText(promptBox.value) }, '클립보드로 복사')));
  promptCard.appendChild(promptBox);
  promptCard.appendChild(savedLine);
  view.appendChild(promptCard);

  view.appendChild(channelCard());
}

function channelCard() {
  const card = h('div', { class: 'card' });
  card.appendChild(h('h3', {}, '채널 정보 (프롬프트에 함께 들어갑니다)'));
  const fields = {
    name: h('input', { type: 'text' }),
    topic: h('input', { type: 'text' }),
    audience: h('textarea', { rows: 2 }),
    tone: h('textarea', { rows: 2 }),
  };
  api('/api/publishing/channel').then((data) => {
    for (const [key, node] of Object.entries(fields)) node.value = data[key] || '';
  }).catch(() => {});
  card.appendChild(h('label', { class: 'field' }, h('span', {}, '채널명'), fields.name));
  card.appendChild(h('label', { class: 'field' }, h('span', {}, '주제'), fields.topic));
  card.appendChild(h('label', { class: 'field' }, h('span', {}, '타겟 시청자'), fields.audience));
  card.appendChild(h('label', { class: 'field' }, h('span', {}, '톤 가이드'), fields.tone));
  card.appendChild(h('div', { class: 'btn-row' },
    h('button', {
      class: 'btn sm', onclick: async () => {
        const body = {};
        for (const [key, node] of Object.entries(fields)) body[key] = node.value;
        try { await api('/api/publishing/channel', { body }); toast('채널 정보를 저장했습니다.', 'ok'); }
        catch (e) { toast(e.message, 'err'); }
      }
    }, '채널 정보 저장')));
  return card;
}

async function copyText(text) {
  if (!text) { toast('복사할 내용이 없습니다.'); return; }
  try { await navigator.clipboard.writeText(text); toast('클립보드에 복사했습니다.', 'ok'); }
  catch (_) {
    const ta = document.createElement('textarea');
    ta.value = text; document.body.appendChild(ta); ta.select();
    document.execCommand('copy'); ta.remove();
    toast('클립보드에 복사했습니다.', 'ok');
  }
}

/* ── 5차 세로용 영상 ─────────────────────────────────────────────────── */
async function viewStage5() {
  const view = document.getElementById('view');
  view.innerHTML = '';

  const card = h('div', { class: 'card' });
  card.appendChild(h('h2', {}, '5차 세로용 영상'));
  view.appendChild(card);

  let data;
  try { data = await api(`/api/publishing/${S.sessionId}/vertical/timeline`); }
  catch (e) { card.appendChild(banner('danger', null, [e.message])); return; }

  card.appendChild(banner('warn', '고른 시각은 컷 편집 후 기준입니다', [data.notice]));

  const startInput = h('input', { type: 'text', value: '00:00:00.000' });
  const endInput = h('input', { type: 'text', value: '00:00:25.000' });
  card.appendChild(h('div', { class: 'grid-2' },
    h('label', { class: 'field' }, h('span', {}, '시작 (편집본 기준)'), startInput),
    h('label', { class: 'field' }, h('span', {}, '끝 (편집본 기준)'), endInput)));

  const subList = h('div', { class: 'table-wrap', style: 'max-height:260px' },
    h('table', {}, h('thead', {}, h('tr', {}, h('th', {}, '#'), h('th', {}, '시작'), h('th', {}, '내용'), h('th', {}, ''))),
      h('tbody', {}, data.subtitles.map((s) => h('tr', {},
        h('td', { class: 'mono' }, String(s.index)),
        h('td', { class: 'mono' }, tc(s.start_us)),
        h('td', {}, s.text),
        h('td', {}, h('div', { class: 'btn-row' },
          h('button', { class: 'btn sm', onclick: () => { startInput.value = tc(s.start_us); } }, '시작'),
          h('button', { class: 'btn sm', onclick: () => { endInput.value = tc(s.end_us); } }, '끝'))))))));
  card.appendChild(h('h3', {}, '자막 타임라인에서 구간 고르기'));
  card.appendChild(subList);

  const rangeInfo = h('div', {});
  card.appendChild(h('div', { class: 'btn-row', style: 'margin-top:var(--sp-3)' },
    h('button', {
      class: 'btn', onclick: async () => {
        const s = parseTime(startInput.value), e = parseTime(endInput.value);
        if (s === null || e === null) { toast('시간 형식을 확인해 주세요.', 'err'); return; }
        try {
          const res = await api(`/api/publishing/${S.sessionId}/vertical/preview-range`, { body: { start_us: s, end_us: e } });
          rangeInfo.innerHTML = '';
          if (res.notes.length) rangeInfo.appendChild(banner('warn', null, res.notes));
          rangeInfo.appendChild(h('p', { class: 'muted' }, `원본에서 ${res.pieces.length}조각 · 총 ${secs(res.duration_us)}`));
          const list = h('div', { class: 'list' });
          res.pieces.forEach((p, i) => list.appendChild(h('div', { class: 'list-row' },
            h('span', { class: 'faint mono' }, String(i + 1)),
            h('span', { class: 'grow' }, h('div', { class: 'name' }, p.source_name), h('div', { class: 'meta' }, p.label)))));
          rangeInfo.appendChild(list);
        } catch (err) { toast(err.message, 'err'); }
      }
    }, '이 구간이 원본에서 몇 조각인지 확인')));
  card.appendChild(rangeInfo);

  const bgRes = await api('/api/files/bg').catch(() => ({ items: [], notice: '' }));
  const bgSelect = h('select', {}, h('option', { value: '' }, '— 배경 없음 —'),
    ...bgRes.items.map((f) => h('option', { value: f.path }, f.name)));
  const scaleInput = h('input', { type: 'number', step: '0.05', value: data.defaults.overlay_scale });
  const yInput = h('input', { type: 'number', step: '0.001', value: data.defaults.overlay_y });
  const subYInput = h('input', { type: 'number', step: '0.0001', value: data.defaults.subtitle_y });
  const nameInput = h('input', { type: 'text', value: `세로_${S.sessionId}` });

  const buildCard = h('div', { class: 'card' });
  buildCard.appendChild(h('h3', {}, '세로 드래프트 만들기 (1080x1920)'));
  if (bgRes.notice) buildCard.appendChild(banner('warn', null, [bgRes.notice]));
  buildCard.appendChild(h('div', { class: 'grid-2' },
    h('label', { class: 'field' }, h('span', {}, '배경 이미지 (최하단, scale 1.0 / 위치 0,0)'), bgSelect),
    h('label', { class: 'field' }, h('span', {}, '드래프트 이름'), nameInput),
    h('label', { class: 'field' }, h('span', {}, '16:9 오버레이 scale (실측 1.8)'), scaleInput),
    h('label', { class: 'field' }, h('span', {}, '오버레이 Y (실측 -0.078125)'), yInput),
    h('label', { class: 'field' }, h('span', {}, `자막 Y (세로 실측 ${data.defaults.subtitle_y})`), subYInput)));
  const buildProgress = progressBox();
  buildCard.appendChild(h('div', { class: 'btn-row' },
    h('button', {
      class: 'btn primary', onclick: async () => {
        const s = parseTime(startInput.value), e = parseTime(endInput.value);
        if (s === null || e === null) { toast('시간 형식을 확인해 주세요.', 'err'); return; }
        try {
          const job = await api(`/api/publishing/${S.sessionId}/vertical/build`, {
            body: {
              start_us: s, end_us: e, draft_name: nameInput.value,
              background_image: bgSelect.value,
              overlay_scale: Number(scaleInput.value),
              overlay_y: Number(yInput.value),
              subtitle_y: Number(subYInput.value),
            },
          });
          runJob(job, buildProgress, async (payload) => {
            toast(payload.label, 'ok');
            showVerified(payload.result.report.details.verified);
            await refreshSession();
          });
        } catch (err) { toast(err.message, 'err'); }
      }
    }, '세로 드래프트 만들기')));
  buildCard.appendChild(buildProgress);
  view.appendChild(buildCard);

  const promptCard = h('div', { class: 'card' });
  promptCard.appendChild(h('h3', {}, '상단·하단 문구 프롬프트'));
  const takeaway = h('textarea', { rows: 2, placeholder: '이 클립의 핵심 메시지 한 줄' });
  const promptBox = h('textarea', { class: 'prompt-box hidden', readonly: true });
  promptCard.appendChild(h('label', { class: 'field' }, h('span', {}, '핵심 메시지'), takeaway));
  promptCard.appendChild(h('p', { class: 'faint' }, '상단 문구 +0.6338 / 하단 문구 -0.7394 (1080x1920 실측, 정규화 좌표)'));
  promptCard.appendChild(h('div', { class: 'btn-row' },
    ...['shorts', 'reels'].map((platform) => h('button', {
      class: 'btn', onclick: async () => {
        const s = parseTime(startInput.value), e = parseTime(endInput.value);
        try {
          const res = await api(`/api/publishing/${S.sessionId}/vertical/prompt`, {
            body: { platform, start_us: s, end_us: e, takeaway: takeaway.value },
          });
          promptBox.value = res.prompt; promptBox.classList.remove('hidden');
          toast(`${platform === 'reels' ? '릴스' : '쇼츠'}용 프롬프트를 만들었습니다. txt 저장: ${res.saved_path}`, 'ok');
        } catch (err) { toast(err.message, 'err'); }
      }
    }, platform === 'reels' ? '릴스용 프롬프트 (프로필 링크 CTA 포함)' : '쇼츠용 프롬프트')),
    h('button', { class: 'btn', onclick: () => copyText(promptBox.value) }, '클립보드로 복사')));
  promptCard.appendChild(promptBox);
  view.appendChild(promptCard);
}

/* ── 세션 목록 ───────────────────────────────────────────────────────── */
async function openSessionList() {
  let data;
  try { data = await api('/api/sessions'); } catch (e) { toast(e.message, 'err'); return; }
  const rows = data.sessions.map((s) => h('div', { class: 'list-row' },
    h('span', { class: 'grow' },
      h('div', { class: 'name' }, s.title),
      h('div', { class: 'meta' }, `${s.updated} · 영상 ${s.source_count}개 · ${s.progress_label}${s.draft_name ? ' · ' + s.draft_name : ''}`)),
    h('button', {
      class: 'btn sm', onclick: async () => {
        localStorage.setItem('capcut_session', s.id);
        closeModal(); S.sessionId = s.id; S.active = 'stage0';
        await refreshSession(); render();
      }
    }, '열기'),
    h('button', {
      class: 'btn sm danger', onclick: async () => {
        if (!confirm(`'${s.title}' 세션을 삭제할까요? 드래프트 파일은 지워지지 않습니다.`)) return;
        try {
          await api(`/api/sessions/${s.id}`, { method: 'DELETE' });
          toast('삭제했습니다.', 'ok'); closeModal(); openSessionList();
          if (s.id === S.sessionId) { localStorage.removeItem('capcut_session'); await boot(); }
        } catch (e) { toast(e.message, 'err'); }
      }
    }, '삭제')));
  modal('세션 목록', [
    data.sessions.length ? h('div', { class: 'list' }, rows) : h('p', { class: 'muted' }, '세션이 없습니다.'),
  ]);
}

/* ── 로그 패널 ───────────────────────────────────────────────────────── */
function setupLogs() {
  const head = document.getElementById('logsHead');
  const body = document.getElementById('logsBody');
  const toggle = document.getElementById('logsToggle');
  head.addEventListener('click', () => {
    body.classList.toggle('hidden');
    toggle.textContent = body.classList.contains('hidden') ? '▸' : '▾';
  });
  setInterval(async () => {
    try {
      const res = await api(`/api/setup/logs?after=${S.logSeq}`);
      for (const row of res.logs) {
        S.logSeq = Math.max(S.logSeq, row.seq);
        body.appendChild(h('div', { class: 'row ' + row.level },
          h('span', { class: 't' }, row.time),
          h('span', { class: 'faint' }, row.name),
          h('span', { class: 'm' }, row.message)));
      }
      while (body.children.length > 400) body.removeChild(body.firstChild);
      if (res.logs.length && !body.classList.contains('hidden')) body.scrollTop = body.scrollHeight;
      document.getElementById('logsCount').textContent = S.logSeq ? `${S.logSeq}줄` : '';
    } catch (_) { /* 폴링 실패는 화면을 방해하지 않습니다 */ }
  }, 2500);
}

/* ── 라우팅 ──────────────────────────────────────────────────────────── */
function render() {
  renderNav();
  document.getElementById('sessionName').textContent = S.session ? S.session.title : '';
  const step = S.steps.find((s) => s.id === S.active);
  if (step && step.locked) {
    document.getElementById('view').innerHTML = '';
    document.getElementById('view').appendChild(h('div', { class: 'card' },
      h('h2', {}, step.title),
      banner('warn', '아직 실행할 수 없습니다', [step.lock_reason])));
    return;
  }
  const views = {
    stage0: viewStage0, calibration: viewCalibration, stage1: viewStage1,
    stage2: viewStage2, stage3: viewStage3, stage4: viewStage4, stage5: viewStage5,
  };
  const fn = views[S.active] || viewStage0;
  Promise.resolve(fn()).catch((e) => {
    document.getElementById('view').innerHTML = '';
    document.getElementById('view').appendChild(h('div', { class: 'card' },
      banner('danger', '화면을 그리지 못했습니다', [e.message])));
  });
}

async function refreshSession(rerender = true) {
  const res = await api(`/api/sessions/${S.sessionId}`);
  S.session = res.session;
  S.steps = res.steps;
  if (rerender) render();
  else renderNav();
}

async function boot() {
  try { S.status = await api('/api/setup/status'); } catch (e) { toast(e.message, 'err'); }

  let list = { sessions: [] };
  try { list = await api('/api/sessions'); } catch (_) { }
  const saved = localStorage.getItem('capcut_session');
  let target = list.sessions.find((s) => s.id === saved) || list.sessions[0];
  if (!target) {
    const created = await api('/api/sessions', { body: {} });
    target = { id: created.id };
  }
  S.sessionId = target.id;
  localStorage.setItem('capcut_session', S.sessionId);
  await refreshSession(false);
  render();
}

document.getElementById('btnNewSession').addEventListener('click', async () => {
  const title = prompt('새 세션 이름을 입력하세요 (비우면 자동으로 정합니다)');
  if (title === null) return;
  try {
    const created = await api('/api/sessions', { body: { title } });
    S.sessionId = created.id; S.active = 'stage0';
    localStorage.setItem('capcut_session', created.id);
    await refreshSession(); toast('새 세션을 만들었습니다.', 'ok');
  } catch (e) { toast(e.message, 'err'); }
});
document.getElementById('btnSessionList').addEventListener('click', openSessionList);

setupLogs();
boot();
