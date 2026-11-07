'use strict';
const OWNER = 'amitseren-droid', REPO = 'radio-news-recorder';
const API = `https://api.github.com/repos/${OWNER}/${REPO}/commits?path=recordings/latest.mp3&per_page=1`;
const RAW = `https://raw.githubusercontent.com/${OWNER}/${REPO}`;
const $ = id => document.getElementById(id);
const audio = $('audio'), play = $('play');
const dateFormat = new Intl.DateTimeFormat('he-IL', {timeZone:'Asia/Jerusalem',day:'2-digit',month:'2-digit',hour:'2-digit',minute:'2-digit'});
const fmt = t => Number.isFinite(t) ? `${Math.floor(t/60)}:${String(Math.floor(t%60)).padStart(2,'0')}` : '0:00';
let current = null, pending = null, busy = false, seeking = false, checkError = '', speed = 1;
function setStatus(text) { $('status').textContent = text; }
function setPlay() {
  play.textContent = audio.paused ? '▶' : '❚❚';
  play.setAttribute('aria-label', audio.paused ? 'נגן' : 'השהה');
}
function isStale(item, now = Date.now()) {
  if (!item) return true;
  if (Number.isFinite(item.target)) {
    const hour = Math.floor(now / 3600000) * 3600000;
    const expected = now - hour >= 12 * 60000 ? hour : hour - 3600000;
    return item.target < expected;
  }
  return !Number.isFinite(item.saved) || now - item.saved > 75 * 60000;
}
function renderStatus() {
  if (!current) { if (checkError) setStatus(checkError); return; }
  const stale = isStale(current);
  const label = dateFormat.format(new Date(Number.isFinite(current.target) ? current.target : current.saved));
  $('recordedAt').textContent = Number.isFinite(current.target) ? `הקלטה לשעה ${label}` : `נשמר ב־${label} (שעת שמירת הקובץ)`;
  $('liveBadge').textContent = stale ? 'ההקלטה אינה עדכנית' : checkError ? 'עדכון לא אומת' : 'מבזק אחרון';
  if (checkError) setStatus(`${checkError} ההקלטה שכבר נטענה עדיין זמינה.`);
  else if (pending) setStatus('יש הקלטה חדשה. ההאזנה הנוכחית לא תיעצר; היא תיטען בסיום.');
  else if (stale) setStatus(`עדיין לא התקבל מבזק חדש. ההקלטה האחרונה הזמינה היא מ־${label}.`);
  else if (current.introDetected === false) setStatus('ההקלטה זמינה; הפתיח לא זוהה בוודאות ולכן לא בוצע חיתוך.');
  else setStatus('ההקלטה מוכנה להאזנה.');
}
async function request(url) {
  const controller = new AbortController();
  const timer = setTimeout(() => controller.abort(), 12000);
  try { return await fetch(url, {cache:'no-store', signal:controller.signal}); }
  finally { clearTimeout(timer); }
}
function applyRecording(item) {
  current = item; pending = null;
  audio.src = item.url; audio.load(); audio.playbackRate = speed;
  $('seek').value = 0; $('elapsed').textContent = '0:00'; $('duration').textContent = '0:00';
  play.disabled = false; setPlay(); renderStatus();
}
async function refresh() {
  if (busy) return;
  busy = true; $('refresh').disabled = true;
  try {
    const response = await request(API);
    if (!response.ok) throw Error(`לא ניתן לאמת עדכונים כרגע (${response.status}).`);
    const commits = await response.json(), commit = commits[0];
    if (!commit || !/^[a-f0-9]{40}$/.test(commit.sha)) throw Error('עדיין לא נמצאה הקלטה זמינה.');
    if (current && current.sha === commit.sha) { checkError = ''; renderStatus(); return; }
    const saved = Date.parse(commit.commit.committer?.date || commit.commit.author?.date);
    if (!Number.isFinite(saved)) throw Error('לא ניתן לאמת את זמן שמירת ההקלטה.');
    // Pin both audio and metadata to the exact same commit, not the mutable main URL.
    const base = `${RAW}/${commit.sha}/recordings`;
    const item = {sha:commit.sha, saved, target:null, introDetected:null, url:`${base}/latest.mp3`};
    try {
      const metadata = await request(`${base}/latest.json`);
      if (metadata.ok) {
        const data = await metadata.json();
        if (Number.isFinite(data.target_epoch) && data.target_epoch > 0 && data.target_epoch*1000 <= saved) {
          item.target = data.target_epoch*1000; item.introDetected = data.intro_detected;
        }
      }
    } catch (_) { /* Old recordings remain playable without new metadata. */ }
    checkError = '';
    if (current && !audio.paused && !audio.ended) { pending = item; renderStatus(); }
    else applyRecording(item);
  } catch (error) {
    checkError = error.name === 'AbortError' ? 'בדיקת העדכונים לא הושלמה בזמן.' : error.message;
    renderStatus();
  } finally { busy = false; $('refresh').disabled = false; }
}
play.addEventListener('click', async () => {
  try { if (audio.paused) await audio.play(); else audio.pause(); }
  catch (_) { setStatus('לא ניתן לנגן את ההקלטה כרגע. נסה לרענן.'); }
});
audio.addEventListener('play', setPlay);
audio.addEventListener('pause', setPlay);
audio.addEventListener('ended', () => { setPlay(); if (pending) applyRecording(pending); });
audio.addEventListener('loadedmetadata', () => { $('duration').textContent = fmt(audio.duration); audio.playbackRate = speed; });
audio.addEventListener('timeupdate', () => {
  if (!seeking && Number.isFinite(audio.duration) && audio.duration > 0) {
    $('seek').value = 100*audio.currentTime/audio.duration;
    $('elapsed').textContent = fmt(audio.currentTime);
  }
});
$('seek').addEventListener('input', () => {
  seeking = true;
  if (Number.isFinite(audio.duration)) $('elapsed').textContent = fmt(audio.duration*$('seek').value/100);
});
$('seek').addEventListener('change', () => {
  if (Number.isFinite(audio.duration)) audio.currentTime = audio.duration*$('seek').value/100;
  seeking = false;
});
const speeds = [1,1.25,1.5,2];
$('speed').addEventListener('click', () => { speed = speeds[(speeds.indexOf(speed)+1)%speeds.length]; audio.playbackRate = speed; $('speed').textContent = `מהירות ×${speed}`; });
$('refresh').addEventListener('click', refresh);
audio.addEventListener('error', () => setStatus('טעינת האודיו נכשלה. נסה לרענן את ההקלטה.'));
document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') refresh(); });
window.addEventListener('pageshow', refresh);
setInterval(() => { if (document.visibilityState === 'visible') refresh(); }, 5*60000);
setInterval(renderStatus, 60000);
refresh();
