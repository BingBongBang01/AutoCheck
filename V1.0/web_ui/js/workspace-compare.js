// ===== 회차 비교 (IMPROVEMENT_PLAN 4-7) =====
// 같은 고객사의 두 점검(회차, 또는 회차 안의 Run)을 점검 항목·장비 정보·구성도 링크로 비교한다. 서버
// (engine/round_compare.py)가 두 점검의 원본 로그에서 보고서·구성도와 같은 판정을 만들어 비교하므로, 보고서를 만들지
// 않은 회차와도 비교된다. 기본 쌍은 이 회차와 바로 앞 회차, Run 이력의 '비교'는 그 Run 과 바로 앞 Run 이다.

const WS_COMPARE_KIND = {
  worse: ['나빠짐', 'badge-fail'], better: ['좋아짐', 'badge-pass'],
  changed: ['값 바뀜', 'badge-neutral'], rebooted: ['재부팅', 'badge-warn'],
};
const WS_COMPARE_LINK_KIND = {
  new: ['새 링크', 'badge-pass'], lost: ['사라진 링크', 'badge-fail'],
  changed: ['상태 바뀜', 'badge-warn'], unverified: ['확인 불가', 'badge-neutral'],
};

function wsCompareValue(source) {
  return source ? JSON.stringify([source.profile, source.run_id || null]) : '';
}

function wsCompareOptions(rounds, selected) {
  const blank = `<option value="" ${selected ? '' : 'selected'}>— 고르세요 —</option>`;
  return blank + rounds.slice().reverse().map(round => {
    const options = [null, ...round.runs].map(runId => {
      const value = wsCompareValue({ profile: round.profile, run_id: runId });
      const text = runId ? `Run ${runId}` : `${round.profile} — 장비별 최신 로그`;
      return `<option value="${esc(value)}" ${value === selected ? 'selected' : ''}>${esc(text)}</option>`;
    }).join('');
    return `<optgroup label="${esc(round.profile + (round.inspection_date ? ` (${round.inspection_date})` : ''))}">${options}</optgroup>`;
  }).join('');
}

async function wsInitCompare(runId = null) {
  const body = document.getElementById('ws-compare-body');
  if (!body) return null;
  const data = await call('get_round_compare_sources', runId);
  if (!data || data.error) {
    body.innerHTML = `<p style="font-size:12px;color:var(--sub);">${esc((data && data.error) || '비교할 점검을 불러오지 못했습니다.')}</p>`;
    return null;
  }
  if (!data.rounds.length) {
    body.innerHTML = '<p style="font-size:12px;color:var(--sub);">점검 로그가 있는 회차가 없습니다 — 점검을 한 뒤 비교할 수 있습니다.</p>';
    return null;
  }
  body.innerHTML = `
    <div style="display:flex;gap:8px;align-items:flex-end;flex-wrap:wrap;">
      <div><label class="field-label">기준 (이전)</label>
        <select class="field" id="ws-compare-base" style="width:260px;">${wsCompareOptions(data.rounds, wsCompareValue(data.base))}</select></div>
      <span class="material-symbols-rounded" style="color:var(--sub);padding-bottom:4px;">arrow_forward</span>
      <div><label class="field-label">비교 (이후)</label>
        <select class="field" id="ws-compare-target" style="width:260px;">${wsCompareOptions(data.rounds, wsCompareValue(data.target))}</select></div>
      <button class="btn btn-primary" id="ws-btn-compare"><span class="material-symbols-rounded">compare_arrows</span>비교</button>
    </div>
    <div id="ws-compare-result" style="margin-top:12px;">${data.base ? ''
      : '<p style="font-size:12px;color:var(--sub);margin:0;">이 회차보다 앞선 점검이 없습니다 — 기준을 직접 고르세요.</p>'}</div>`;
  document.getElementById('ws-btn-compare').addEventListener('click', wsRunCompare);
  return data;
}

// Run 이력의 '비교' — 그 Run 과 바로 앞 Run(없으면 앞 회차)을 바로 비교한다.
async function wsCompareRun(runId) {
  const data = await wsInitCompare(runId);
  const card = document.getElementById('ws-compare-card');
  if (card) card.scrollIntoView({ behavior: 'smooth', block: 'start' });
  if (data && data.base) await wsRunCompare();
  else if (data) showToast('이 Run 보다 앞선 점검이 없습니다 — 기준을 직접 고르세요', 'warn');
}

async function wsRunCompare() {
  const pick = id => { const value = document.getElementById(id).value; return value ? JSON.parse(value) : null; };
  const base = pick('ws-compare-base');
  const target = pick('ws-compare-target');
  const out = document.getElementById('ws-compare-result');
  if (!base || !target) { showToast('비교할 두 점검을 고르세요', 'warn'); return; }
  out.innerHTML = '<p style="font-size:12px;color:var(--sub);">비교하는 중... (두 점검의 로그를 다시 판정합니다)</p>';
  const data = await call('compare_rounds', { profile: base[0], run_id: base[1] }, { profile: target[0], run_id: target[1] });
  if (!data || data.error) {
    out.innerHTML = `<p style="font-size:12px;color:var(--critical);">${esc((data && data.error) || '비교하지 못했습니다.')}</p>`;
    return;
  }
  out.innerHTML = wsCompareResultHtml(data);
}

function wsCompareSide(side) {
  return `<b>${esc(side.label)}</b> (${esc((side.collected_at || '').replace('T', ' '))} · 장비 ${esc(side.device_count)}대 · 링크 ${esc(side.link_count)}개)`;
}

function wsCompareLinkEnd(device, port) {
  return `${esc(device)} <span class="mono" style="color:var(--sub);">${esc(port || '')}</span>`;
}

function wsCompareResultHtml(data) {
  const c = data.counts || {};
  const head = `<p style="font-size:12px;color:var(--sub);margin:0 0 8px;">기준 ${wsCompareSide(data.base)} → 비교 ${wsCompareSide(data.target)} · 둘 다 점검한 장비 ${esc(data.common_devices)}대</p>`;
  if (data.unchanged) return `${head}<p style="font-size:13px;margin:0;">달라진 것이 없습니다.</p>`;
  const chips = [
    ['나빠진 항목', c.worse, 'badge-fail'], ['좋아진 항목', c.better, 'badge-pass'], ['값이 바뀐 항목', c.changed, 'badge-neutral'],
    ['재부팅', c.rebooted, 'badge-warn'], ['장비 정보 변경', c.facts, 'badge-warn'],
    ['새 링크', c.links_new, 'badge-pass'], ['사라진 링크', c.links_lost, 'badge-fail'], ['상태 바뀐 링크', c.links_changed, 'badge-warn'],
    ['확인 불가 링크', c.links_unverified, 'badge-neutral'],
    ['기준에만 있는 장비', c.only_base, 'badge-neutral'], ['비교에만 있는 장비', c.only_target, 'badge-neutral'],
  ].filter(([, count]) => count).map(([text, count, tone]) => `<span class="badge ${tone}">${esc(text)} ${esc(count)}</span>`).join(' ');
  const parts = [head, `<div style="display:flex;gap:6px;flex-wrap:wrap;margin-bottom:10px;">${chips}</div>`];

  if (data.items.length) {
    parts.push(wsCompareTable('점검 항목', ['장비', '항목', '기준', '비교', '구분'], data.items.map(row => {
      const [text, tone] = WS_COMPARE_KIND[row.kind] || [row.kind, 'badge-neutral'];
      return `<tr><td>${esc(row.device)}</td><td>${esc(row.item)}</td>
        <td style="white-space:pre-line;">${esc(row.before)}<div style="color:var(--sub);font-size:11px;">${esc(row.before_status)}</div></td>
        <td style="white-space:pre-line;">${esc(row.after)}<div style="color:var(--sub);font-size:11px;">${esc(row.after_status)}</div></td>
        <td><span class="badge ${tone}">${esc(text)}</span></td></tr>`;
    })));
  }
  if (data.facts.length) {
    parts.push(wsCompareTable('장비 정보', ['장비', '항목', '기준', '비교'], data.facts.map(row =>
      `<tr><td>${esc(row.device)}</td><td>${esc(row.field)}</td><td class="mono">${esc(row.before)}</td><td class="mono">${esc(row.after)}</td></tr>`)));
  }
  const links = ['new', 'lost', 'changed', 'unverified'].flatMap(kind => (data.links[kind] || []).map(link => ({ kind, link })));
  if (links.length) {
    parts.push(wsCompareTable('구성도 링크', ['구분', '한쪽 끝', '다른 끝', '비고'], links.map(({ kind, link }) => {
      const [text, tone] = WS_COMPARE_LINK_KIND[kind];
      const note = kind === 'changed' ? `${link.before} → ${link.after}`
        : kind === 'unverified' ? (link.side === 'base' ? '비교 점검에서 양 끝 모두 이웃 정보가 없음' : '기준 점검에서 양 끝 모두 이웃 정보가 없음')
        : (link.bundle || '');
      return `<tr><td><span class="badge ${tone}">${esc(text)}</span></td><td>${wsCompareLinkEnd(link.a, link.a_port)}</td>
        <td>${wsCompareLinkEnd(link.b, link.b_port)}</td><td>${esc(note)}</td></tr>`;
    })));
  }
  if (data.devices.only_base.length || data.devices.only_target.length) {
    parts.push(`<p style="font-size:12px;margin:8px 0 0;">${data.devices.only_base.length
      ? `기준에만 있는 장비: ${esc(data.devices.only_base.join(', '))}<br>` : ''}${data.devices.only_target.length
      ? `비교에만 있는 장비: ${esc(data.devices.only_target.join(', '))}` : ''}</p>`);
  }
  return parts.join('');
}

function wsCompareTable(title, headers, rows) {
  return `<p class="card-title" style="font-size:13px;margin:12px 0 4px;">${esc(title)} (${rows.length})</p>
    <div class="table-scroll"><table class="dtable"><thead><tr>${headers.map(h => `<th>${esc(h)}</th>`).join('')}</tr></thead>
    <tbody>${rows.join('')}</tbody></table></div>`;
}
