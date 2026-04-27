import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, render_template, request, redirect, url_for, flash, session
from datetime import datetime
from functools import wraps
from cachetools import cached, TTLCache

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_TYPE'] = 'filesystem'

cache = TTLCache(maxsize=10, ttl=30)

SERVICE_ACCOUNT_FILE = 'credentials.json'
SCOPE = ["https://spreadsheets.google.com/feeds", "https://www.googleapis.com/auth/drive"]
SPREADSHEET_ID = '1Nuk53a84vNg7I-I6heXDaZCOXS1Bs6-Pc-5mZYCDqIQ'
SHEET_EQUIPMENT = 'Оборудование'
SHEET_MOVEMENTS = 'Движения'
ADMIN_PASSWORD = "sklad2025"

def get_gsheet(sheet_name):
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, SCOPE)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)

@cached(cache)
def get_equipment_list():
    ws = get_gsheet(SHEET_EQUIPMENT)
    data = ws.get_all_values()
    if not data or len(data) < 2:
        return []
    headers = data[0]
    name_col = total_col = None
    for i, h in enumerate(headers):
        h_low = h.strip().lower()
        if 'название' in h_low:
            name_col = i
        if 'общее количество' in h_low:
            total_col = i
    if name_col is None: name_col = 0
    if total_col is None: total_col = 1
    items = []
    for row in data[1:]:
        if len(row) <= max(name_col, total_col):
            continue
        name = row[name_col].strip()
        if not name:
            continue
        try:
            total = int(float(row[total_col].strip()))
        except:
            total = 0
        items.append({'name': name, 'total': total})
    return items

# @cached(cache)
def get_current_stock():
    """Возвращает словарь {название: {'total': N, 'locations': {локация: кол-во}}}"""
    equipment = get_equipment_list()  # список [{'name': '...', 'total': ...}]
    stock = {item['name']: {'total': item['total'], 'locations': {}} for item in equipment}
    
    mov_ws = get_gsheet(SHEET_MOVEMENTS)
    data = mov_ws.get_all_values()
    if len(data) < 2:
        return stock
    headers = data[0]
    try:
        col_eq = headers.index('Оборудование')
        col_dest = headers.index('Куда')
        col_qty = headers.index('Количество')
    except ValueError:
        col_eq, col_dest, col_qty = 0, 1, 2
    
    for row in data[1:]:
        if len(row) <= max(col_eq, col_dest, col_qty):
            continue
        eq = row[col_eq].strip()
        if not eq or eq not in stock:
            continue
        dest = row[col_dest].strip()
        if not dest or dest.lower() == 'склад':
            continue
        try:
            qty = int(float(row[col_qty].strip()))
        except:
            continue
        if qty == 0:
            continue
        stock[eq]['locations'][dest] = stock[eq]['locations'].get(dest, 0) + qty
    
    for eq in stock:
        stock[eq]['locations'] = {loc: q for loc, q in stock[eq]['locations'].items() if q > 0}
    return stock

@cached(cache)
def get_all_locations():
    stock = get_current_stock()
    locs = {}
    for eq, data in stock.items():
        for loc, qty in data['locations'].items():
            if qty > 0:
                locs.setdefault(loc, {})[eq] = qty
    return locs

def add_movement(equipment, destination, quantity, responsible='', comment=''):
    ws = get_gsheet(SHEET_MOVEMENTS)
    ws.append_row([equipment, destination, quantity, datetime.now().strftime('%Y-%m-%d %H:%M:%S'), responsible, comment])
    cache.clear()

def login_required(f):
    @wraps(f)
    def decorated(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST' and request.form.get('password') == ADMIN_PASSWORD:
        session['logged_in'] = True
        flash('Добро пожаловать!', 'success')
        return redirect(url_for('index'))
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Вы вышли', 'info')
    return redirect(url_for('login'))

@app.route('/')
@login_required
def index():
    stock = get_current_stock()
    for n, d in stock.items():
        d['stock'] = d['total'] - sum(d['locations'].values())
    return render_template('index.html', stock=stock)

@app.route('/locations')
@login_required
def locations():
    # Берём текущие остатки (оборудование -> локации)
    stock = get_current_stock()
    # Строим словарь: локация -> {оборудование: количество}
    locations_dict = {}
    for eq_name, eq_data in stock.items():
        for loc_name, qty in eq_data['locations'].items():
            if qty > 0:  # только положительные количества
                if loc_name not in locations_dict:
                    locations_dict[loc_name] = {}
                locations_dict[loc_name][eq_name] = qty
    return render_template('locations.html', locations=locations_dict)
@app.route('/move', methods=['GET', 'POST'])
@login_required
def move():
    if request.method == 'POST':
        destination = request.form.get('destination', '').strip()
        if not destination:
            flash('Укажите локацию', 'danger')
            return redirect(request.url)
        responsible = request.form.get('responsible', '')
        comment = request.form.get('comment', '')
        items = request.form.getlist('equipment[]')
        quantities = request.form.getlist('quantity[]')
        errors = False
        for eq, qty_str in zip(items, quantities):
            if not eq or not qty_str:
                continue
            try:
                qty = int(qty_str)
                if qty <= 0:
                    flash(f'Количество для "{eq}" должно быть >0', 'danger')
                    errors = True
            except ValueError:
                flash(f'Некорректное количество для "{eq}"', 'danger')
                errors = True
                continue
            stock = get_current_stock()
            if eq not in stock:
                flash(f'Оборудование "{eq}" не найдено', 'danger')
                errors = True
                continue
            available = stock[eq]['total'] - sum(stock[eq]['locations'].values())
            if available < qty:
                flash(f'Недостаточно "{eq}" (доступно: {available})', 'danger')
                errors = True
        if errors:
            return redirect(request.url)
        for eq, qty_str in zip(items, quantities):
            if eq and qty_str:
                add_movement(eq, destination, int(qty_str), responsible, comment)
        flash('Перемещение выполнено', 'success')
        return redirect(url_for('index'))
    
    # ---- GET: формируем список названий оборудования ----
    equipment_names = []
    try:
        # Пытаемся использовать get_equipment_list
        for item in get_equipment_list():
            equipment_names.append(item['name'])
    except:
        # Если не работает, читаем напрямую из таблицы
        try:
            ws = get_gsheet(SHEET_EQUIPMENT)
            data = ws.get_all_values()
            if data and len(data) > 1:
                # ищем колонку с названием
                name_col = 0
                for i, h in enumerate(data[0]):
                    if 'название' in h.lower():
                        name_col = i
                        break
                for row in data[1:]:
                    if len(row) > name_col and row[name_col].strip():
                        equipment_names.append(row[name_col].strip())
        except:
            pass
    
    return render_template('move.html', equipment_names=equipment_names)

@app.route('/receiving', methods=['GET', 'POST'])
@login_required
def receiving():
    if request.method == 'POST':
        responsible = request.form.get('responsible', '')
        comment = request.form.get('comment', '')
        eqs = request.form.getlist('equipment[]')
        locs = request.form.getlist('location[]')
        qts = request.form.getlist('quantity[]')
        for eq, loc, qs in zip(eqs, locs, qts):
            if not eq or not loc or not qs: continue
            try:
                q = int(qs)
                if q <= 0: continue
            except:
                continue
            add_movement(eq, loc, -q, responsible, comment)
        cache.clear()
        flash('Возврат оформлен', 'success')
        return redirect(url_for('index'))
    stock = get_current_stock()
    items = []
    for eq, data in stock.items():
        for loc, qty in data['locations'].items():
            if qty > 0:
                items.append({'equipment': eq, 'location': loc, 'quantity': qty})
    return render_template('receiving.html', items=items)

if __name__ == '__main__':
    app.run(debug=True)