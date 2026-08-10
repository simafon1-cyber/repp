"""
web_dashboard.py — локальный веб-дашборд (Flask), запускается фоновым потоком
внутри main.py. Открывается с телефона по локальной Wi-Fi сети:
http://<IP-адрес-этого-компьютера>:5000

Весь дашборд (и просмотр, и кнопки) закрыт логином/паролем (HTTP Basic Auth,
DASHBOARD_LOGIN/DASHBOARD_PASSWORD из config.py) — браузер на телефоне
покажет системное окно входа при первом заходе на страницу.

Показывает: баланс/эквити, дневной P/L и просадку, винрейт/профит-фактор,
мини-график equity (чистый canvas, без внешних библиотек), score/AI/режим
рынка по каждому символу, открытые сделки (с закрытием по кнопке), полный
лог сделок. Профиль риска и режим торговли можно менять прямо тут — без
перезапуска программы (см. control.py: set_risk_profile/set_trading_mode).

ВАЖНО: этот модуль НИКОГДА не вызывает MetaTrader5 напрямую. Он только читает
dashboard_state.get_snapshot() и кладёт заявки на действия в control.control
(очередь/переопределения) — реальные вызовы MT5 исполняет главный цикл в
main.py, в своём потоке (см. докстринг в control.py).
"""

import csv
import os

from flask import Flask, jsonify, request, Response

import config as cfg
import dashboard_state as ds
import secure_store
from control import control

app = Flask(__name__)


def password_is_set() -> bool:
    """Задан ли вообще пароль дашборда.

    ЗАЧЕМ ЭТО ОТДЕЛЬНО. Раньше пустой пароль в настройках работал как
    настоящий: проверка сводилась к `password == ""`, и любой, кто прислал
    правильный логин с пустым паролем, попадал внутрь. Логин — это адрес
    почты владельца, он лежит в config.py и секретом не является.

    Дома, в своей сети, это было терпимо. Но дашборд слушает на всех адресах
    (0.0.0.0), и при переносе программы на облачный сервер с публичным
    адресом та же дыра означала бы, что управление торговлей — старт, стоп,
    смена инструментов — доступно любому, кто нашёл открытый порт.

    Поэтому: пароля нет — дашборда нет. Это безопаснее, чем «работает, но
    пускает всех»."""
    return bool(getattr(cfg, "DASHBOARD_PASSWORD_HASH", "")
                or getattr(cfg, "DASHBOARD_PASSWORD", ""))


def _check_auth(username: str, password: str) -> bool:
    if not password_is_set():
        return False           # см. password_is_set(): пустой пароль — не пароль
    if username != cfg.DASHBOARD_LOGIN:
        return False
    # Новый формат (см. secure_store.py): пароль хранится только как хэш —
    # проверяем через него. Старый формат (DASHBOARD_PASSWORD открытым
    # текстом) — как раньше, для конфигов, ещё не прошедших миграцию.
    stored_hash = getattr(cfg, "DASHBOARD_PASSWORD_HASH", "")
    if stored_hash:
        salt = getattr(cfg, "SECURITY_SALT", "")
        return secure_store.verify_password(password, salt, stored_hash)
    return password == getattr(cfg, "DASHBOARD_PASSWORD", "")


def _auth_required_response():
    return Response(
        "Нужен логин/пароль от дашборда.", 401,
        {"WWW-Authenticate": 'Basic realm="AI Scalper Dashboard"'},
    )


def _no_password_response():
    """Отдельный ответ, а не просто 401: человек должен понять, что дело не в
    забытом пароле, а в том, что пароль вообще не задан."""
    return Response(
        "Пароль дашборда не задан, поэтому дашборд выключен.\n\n"
        "Задайте его в программе: вкладка «Система» -> «Пароль дашборда», "
        "либо DASHBOARD_PASSWORD в config.py, и перезапустите программу.\n\n"
        "Раньше пустой пароль пускал любого, кто знает логин. Логин — это "
        "адрес почты, он лежит в config.py и секретом не является. На "
        "домашнем компьютере это было терпимо, на сервере с публичным "
        "адресом означало бы, что торговлей может управлять кто угодно.",
        403, {"Content-Type": "text/plain; charset=utf-8"})


@app.before_request
def _require_login():
    if not password_is_set():
        return _no_password_response()
    auth = request.authorization
    if not auth or not _check_auth(auth.username, auth.password):
        return _auth_required_response()


PAGE = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>AI Scalper — дашборд</title>
<style>
  :root { color-scheme: dark; }
  * { box-sizing: border-box; }
  body { font-family: -apple-system, Segoe UI, Roboto, sans-serif; background:#111; color:#eee;
         margin:0; padding:12px; font-size:15px; }
  h1 { font-size:18px; margin:4px 0 12px; }
  h3 { margin:0 0 6px; font-size:14px; color:#ccc; }
  .card { background:#1b1b1b; border-radius:10px; padding:10px 12px; margin-bottom:10px; }
  .row { display:flex; justify-content:space-between; flex-wrap:wrap; gap:6px; align-items:center; }
  .grid { display:grid; grid-template-columns:1fr 1fr; gap:6px 14px; font-size:13px; }
  .badge { display:inline-block; padding:2px 8px; border-radius:6px; font-size:12px; font-weight:600; }
  .badge.live { background:#7a1f1f; color:#fff; }
  .badge.dry { background:#2a5; color:#fff; }
  .badge.paused { background:#a56a00; color:#fff; }
  table { width:100%; border-collapse:collapse; font-size:12.5px; }
  th, td { text-align:left; padding:5px 4px; border-bottom:1px solid #2a2a2a; white-space:nowrap; }
  th { color:#999; font-weight:500; }
  .buy { color:#4caf50; } .sell { color:#f44336; }
  .scrollx { overflow-x:auto; }
  select { background:#222; color:#eee; border:1px solid #555; border-radius:6px; padding:6px; font-size:13px; }
  button { background:#333; color:#eee; border:1px solid #555; border-radius:6px;
           padding:8px 14px; font-size:14px; }
  button.danger { background:#7a1f1f; border-color:#a33; }
  button.warn { background:#7a5a00; border-color:#a80; }
  button.small { padding:4px 8px; font-size:12px; }
  .muted { color:#888; font-size:12px; }
  canvas { width:100%; height:70px; display:block; }
</style>
</head>
<body>
  <h1>AI Scalper — дашборд <span id="updatedAt" class="muted"></span></h1>

  <div class="card" id="accountCard">Загрузка...</div>

  <div class="card">
    <h3>Equity</h3>
    <canvas id="equityChart" height="70"></canvas>
  </div>

  <div class="card">
    <h3>Статистика</h3>
    <div class="grid" id="statsGrid"></div>
  </div>

  <div class="card">
    <div class="row">
      <button id="pauseBtn" class="warn" onclick="togglePause()">...</button>
    </div>
    <div class="row" style="margin-top:10px;">
      <div>
        <div class="muted">Профиль риска</div>
        <select id="profileSelect">
          <option value="conservative">Консервативный</option>
          <option value="balanced">Сбалансированный</option>
          <option value="aggressive">Агрессивный</option>
          <option value="hysteric">Истеричка (YOLO)</option>
        </select>
        <button class="small" onclick="applyProfile()">Применить</button>
      </div>
      <div>
        <div class="muted">Режим торговли</div>
        <select id="modeSelect">
          <option value="scalping">Скальпинг</option>
          <option value="news_trading">Новости</option>
          <option value="both">Оба</option>
        </select>
        <button class="small" onclick="applyMode()">Применить</button>
      </div>
    </div>
  </div>

  <div class="card">
    <h3>Символы — выбор пары и лот</h3>
    <div class="muted" style="margin-bottom:6px;">
      Галочка выключает НОВЫЕ входы по паре (открытые сделки продолжают вестись).
      Лот &gt; 0 — фиксированный размер вместо расчёта по риск-профилю; 0 — авторасчёт.
    </div>
    <div class="scrollx">
    <table id="symbolsTable"><thead><tr>
      <th>Вкл</th><th>Символ</th><th>Лот</th><th></th><th>BUY</th><th>SELL</th><th>Режим рынка</th>
      <th>AI</th><th>Автообучение</th><th>Пауза</th><th>Отказ</th>
    </tr></thead><tbody></tbody></table>
    </div>
  </div>

  <div class="card">
    <h3>Открытые сделки</h3>
    <div class="scrollx">
    <table id="positionsTable"><thead><tr>
      <th>Символ</th><th>Тип</th><th>Лот</th><th>Открытие</th><th>SL</th><th>TP</th><th>Профит</th><th>Источник</th><th></th>
    </tr></thead><tbody></tbody></table>
    </div>
  </div>

  <div class="card">
    <h3>Лог сделок (последние 50)</h3>
    <div class="scrollx">
    <table id="logTable"><thead><tr>
      <th>Время</th><th>Событие</th><th>Символ</th><th>Напр.</th><th>Цена</th><th>SL</th><th>TP</th><th>Лот</th><th>Score</th><th>Профит</th>
    </tr></thead><tbody></tbody></table>
    </div>
  </div>

<script>
async function togglePause() {
  const r = await fetch('/api/toggle_pause', {method:'POST'});
  if (r.status === 401) { alert('Сессия входа истекла — обнови страницу.'); return; }
  refresh();
}

async function closePosition(ticket) {
  if (!confirm('Закрыть позицию #' + ticket + ' по рынку?')) return;
  const r = await fetch('/api/close_position', {method:'POST',
    headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ticket: ticket})});
  if (r.status === 401) { alert('Сессия входа истекла — обнови страницу.'); return; }
  refresh();
}

async function applyProfile() {
  const v = document.getElementById('profileSelect').value;
  await fetch('/api/set_profile', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({profile: v})});
  refresh();
}

async function applyMode() {
  const v = document.getElementById('modeSelect').value;
  await fetch('/api/set_mode', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({mode: v})});
  refresh();
}

async function toggleSymbolEnabled(sym, checkbox) {
  await fetch('/api/set_symbol_enabled', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({symbol: sym, enabled: checkbox.checked})});
  refresh();
}

async function applyLot(sym) {
  const input = document.getElementById('lot_' + sym);
  const lot = parseFloat(input.value || '0');
  await fetch('/api/set_lot', {method:'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({symbol: sym, lot: lot})});
  refresh();
}

function drawEquityChart(history) {
  const canvas = document.getElementById('equityChart');
  const ctx = canvas.getContext('2d');
  const w = canvas.clientWidth || 300, h = 70;
  canvas.width = w; canvas.height = h;
  ctx.clearRect(0, 0, w, h);
  if (!history || history.length < 2) {
    ctx.fillStyle = '#666'; ctx.font = '12px sans-serif';
    ctx.fillText('Копится история...', 8, h/2);
    return;
  }
  const values = history.map(p => p.equity);
  const min = Math.min(...values), max = Math.max(...values);
  const range = (max - min) || 1;
  const stepX = w / (values.length - 1);
  ctx.strokeStyle = '#4caf50'; ctx.lineWidth = 2; ctx.beginPath();
  values.forEach((v, i) => {
    const x = i * stepX;
    const y = h - ((v - min) / range) * (h - 10) - 5;
    if (i === 0) ctx.moveTo(x, y); else ctx.lineTo(x, y);
  });
  ctx.stroke();
  ctx.fillStyle = '#888'; ctx.font = '10px sans-serif';
  ctx.fillText(min.toFixed(2), 2, h - 2);
  ctx.fillText(max.toFixed(2), 2, 10);
}

async function refresh() {
  try {
    const res = await fetch('/api/status');
    const s = await res.json();

    document.getElementById('updatedAt').textContent = s.updated_at ? ('обновлено ' + s.updated_at) : '';

    const acc = s.account || {};
    const modeBadge = s.live_trading
      ? '<span class="badge live">LIVE</span>' : '<span class="badge dry">DRY-RUN</span>';
    const pauseBadge = s.paused ? ' <span class="badge paused">ПАУЗА</span>' : '';
    document.getElementById('accountCard').innerHTML =
      `Счёт <b>${acc.login ?? '-'}</b> (${acc.server ?? '-'}) ${modeBadge}${pauseBadge}<br>` +
      `Баланс: <b>${(acc.balance ?? 0).toFixed(2)} ${acc.currency ?? ''}</b> | ` +
      `Эквити: <b>${(acc.equity ?? 0).toFixed(2)}</b><br>` +
      `Профиль: ${s.risk_profile ?? '-'} | Режим: ${s.trading_mode ?? '-'} | Сделок сегодня: ${s.trades_today ?? 0}`;

    document.getElementById('pauseBtn').textContent = s.paused ? 'Возобновить торговлю' : 'Пауза (новые сделки)';

    drawEquityChart(s.equity_history || []);

    const st = s.stats || {};
    const dayColor = (st.day_pnl_pct ?? 0) >= 0 ? 'buy' : 'sell';
    document.getElementById('statsGrid').innerHTML = `
      <div>Сделок всего: <b>${st.total_trades ?? 0}</b></div>
      <div>Винрейт: <b>${st.win_rate ?? 0}%</b></div>
      <div>Профит-фактор: <b>${st.profit_factor ?? 0}</b></div>
      <div>P/L за день: <b class="${dayColor}">${st.day_pnl_pct ?? 0}%</b></div>
      <div>Просадка: <b>${st.drawdown_pct ?? 0}%</b></div>
      <div>Профит/убыток: <b class="buy">+${st.gross_profit ?? 0}</b> / <b class="sell">${st.gross_loss ?? 0}</b></div>
    `;

    const symBody = document.querySelector('#symbolsTable tbody');
    symBody.innerHTML = '';
    for (const [sym, sy] of Object.entries(s.symbols || {})) {
      const tr = document.createElement('tr');
      const checked = sy.enabled === false ? '' : 'checked';
      const lotVal = sy.lot_override ? sy.lot_override : '';
      tr.innerHTML = `<td><input type="checkbox" ${checked} onchange="toggleSymbolEnabled('${sym}', this)"></td>
        <td>${sym}</td>
        <td><input id="lot_${sym}" type="number" step="0.01" min="0" placeholder="авто"
              value="${lotVal}" style="width:70px; background:#222; color:#eee; border:1px solid #555; border-radius:6px; padding:4px;"></td>
        <td><button class="small" onclick="applyLot('${sym}')">Ок</button></td>
        <td class="buy">${(sy.buy_score ?? 0).toFixed(1)}</td>
        <td class="sell">${(sy.sell_score ?? 0).toFixed(1)}</td><td>${sy.regime ?? '-'}</td>
        <td>${sy.ai_direction || '-'} (${Math.round((sy.ai_confidence||0)*100)}%)</td>
        <td class="muted">${sy.learning_status ?? '-'}</td>
        <td class="muted">${sy.paused_until ? ('до ' + sy.paused_until) : '-'}</td>
        <td class="muted">${sy.reject_reason ?? '-'}</td>`;
      symBody.appendChild(tr);
    }

    const posBody = document.querySelector('#positionsTable tbody');
    posBody.innerHTML = '';
    for (const p of (s.positions || [])) {
      const tr = document.createElement('tr');
      const profitColor = p.profit >= 0 ? 'buy' : 'sell';
      tr.innerHTML = `<td>${p.symbol}</td><td>${p.type}</td><td>${p.volume}</td>
        <td>${(p.price_open ?? 0)}</td><td>${(p.sl ?? 0)}</td><td>${(p.tp ?? 0)}</td>
        <td class="${profitColor}">${p.profit.toFixed(2)}</td>
        <td class="muted">${p.is_bot === false ? 'Ручная' : 'Бот'}</td>
        <td><button class="danger small" onclick="closePosition(${p.ticket})">Закрыть</button></td>`;
      posBody.appendChild(tr);
    }
    if ((s.positions || []).length === 0) {
      posBody.innerHTML = '<tr><td colspan="9" class="muted">Нет открытых сделок</td></tr>';
    }

    const logRes = await fetch('/api/log');
    const logRows = await logRes.json();
    const logBody = document.querySelector('#logTable tbody');
    logBody.innerHTML = '';
    for (const row of logRows) {
      const tr = document.createElement('tr');
      const profitVal = parseFloat(row.Profit || '0');
      const profitColor = profitVal >= 0 ? 'buy' : 'sell';
      tr.innerHTML = `<td>${row.Time || ''}</td><td>${row.Event || ''}</td>
        <td>${row.Symbol || ''}</td><td>${row.Direction || ''}</td>
        <td>${row.Price || ''}</td><td>${row.SL || ''}</td><td>${row.TP || ''}</td>
        <td>${row.Lot || ''}</td><td>${row.Score || ''}</td>
        <td class="${profitColor}">${row.Profit || ''}</td>`;
      logBody.appendChild(tr);
    }
  } catch (e) {
    console.error(e);
  }
}

refresh();
setInterval(refresh, 4000);
</script>
</body>
</html>
"""


@app.route("/")
def index():
    return Response(PAGE, mimetype="text/html")


@app.route("/api/status")
def api_status():
    snap = ds.get_snapshot()
    snap["paused"] = control.is_paused()
    return jsonify(snap)


@app.route("/api/log")
def api_log():
    rows = []
    try:
        if os.path.exists(cfg.LOG_CSV_PATH):
            with open(cfg.LOG_CSV_PATH, encoding="utf-8") as f:
                reader = list(csv.reader(f, delimiter=";"))
            if len(reader) > 1:
                header, data = reader[0], reader[1:]
                data = data[-50:]
                rows = [dict(zip(header, r)) for r in reversed(data)]
    except Exception:
        pass
    return jsonify(rows)


@app.route("/api/toggle_pause", methods=["POST"])
def api_toggle_pause():
    control.set_paused(not control.is_paused())
    return jsonify({"paused": control.is_paused()})


@app.route("/api/close_position", methods=["POST"])
def api_close_position():
    data = request.get_json(force=True, silent=True) or {}
    ticket = data.get("ticket")
    if ticket is None:
        return jsonify({"error": "ticket required"}), 400
    control.request_close(int(ticket))
    return jsonify({"queued": True})


@app.route("/api/set_profile", methods=["POST"])
def api_set_profile():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("profile")
    try:
        profile_enum = cfg.RiskProfile(name)
    except ValueError:
        return jsonify({"error": "unknown profile"}), 400
    control.set_risk_profile(profile_enum)
    return jsonify({"ok": True, "profile": profile_enum.value})


@app.route("/api/set_mode", methods=["POST"])
def api_set_mode():
    data = request.get_json(force=True, silent=True) or {}
    name = data.get("mode")
    try:
        mode_enum = cfg.TradingMode(name)
    except ValueError:
        return jsonify({"error": "unknown mode"}), 400
    control.set_trading_mode(mode_enum)
    return jsonify({"ok": True, "mode": mode_enum.value})


@app.route("/api/set_symbol_enabled", methods=["POST"])
def api_set_symbol_enabled():
    data = request.get_json(force=True, silent=True) or {}
    symbol = data.get("symbol")
    enabled = bool(data.get("enabled", True))
    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    control.set_symbol_enabled(symbol, enabled)
    return jsonify({"ok": True, "symbol": symbol, "enabled": enabled})


@app.route("/api/set_lot", methods=["POST"])
def api_set_lot():
    data = request.get_json(force=True, silent=True) or {}
    symbol = data.get("symbol")
    lot = data.get("lot")
    if not symbol:
        return jsonify({"error": "symbol required"}), 400
    try:
        lot = float(lot) if lot is not None else 0.0
    except (TypeError, ValueError):
        return jsonify({"error": "invalid lot"}), 400
    control.set_lot_override(symbol, lot)
    return jsonify({"ok": True, "symbol": symbol, "lot": lot})


def run_dashboard():
    app.run(host="0.0.0.0", port=cfg.DASHBOARD_PORT, debug=False, use_reloader=False)
