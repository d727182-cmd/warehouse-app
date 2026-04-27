import gspread
from oauth2client.service_account import ServiceAccountCredentials
from flask import Flask, render_template, request, redirect, url_for, flash, session
from datetime import datetime
from functools import wraps
from cachetools import cached, TTLCache   # для кеширования

app = Flask(__name__)
app.secret_key = 'your-secret-key-change-in-production'
app.config['SESSION_PERMANENT'] = False
app.config['SESSION_TYPE'] = 'filesystem'

# Создаём кеш на 30 секунд (данные будут обновляться раз в полминуты)
cache = TTLCache(maxsize=10, ttl=30)

# ---------- НАСТРОЙКА GOOGLE SHEETS ----------
SERVICE_ACCOUNT_FILE = 'credentials.json'
SCOPE = [
    "https://spreadsheets.google.com/feeds",
    "https://www.googleapis.com/auth/drive"
]
SPREADSHEET_ID = '1Nuk53a84vNg7I-I6heXDaZCOXS1Bs6-Pc-5mZYCDqIQ'  # ваш ID
SHEET_EQUIPMENT = 'Оборудование'
SHEET_MOVEMENTS = 'Движения'

# ---------- ПАРОЛЬ ----------
ADMIN_PASSWORD = "sklad2025"

# ---------- ФУНКЦИЯ ПОДКЛЮЧЕНИЯ ----------
def get_gsheet(sheet_name):
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, SCOPE)
    client = gspread.authorize(creds)
    return client.open_by_key(SPREADSHEET_ID).worksheet(sheet_name)

# ---------- ОСНОВНЫЕ ФУНКЦИИ (с кешированием) ----------
@cached(cache)
def get_current_stock():
    """Возвращает словарь: {название: {'total': N, 'locations': {локация: кол-во}}}"""
    sheet_eq = get_gsheet(SHEET_EQUIPMENT)
    records = sheet_eq.get_all_records()
    stock = {}
    for row in records:
        name = row.get('Название')
        if not name:
            continue
        total = int(row.get('Общее количество', 0))
        stock[name] = {'total': total, 'locations': {}}
    
    sheet_mov = get_gsheet(SHEET_MOVEMENTS)
    moves = sheet_mov.get_all_records()
    for move in moves:
        eq = move.get('Оборудование')
        if eq not in stock:
            continue
        dest = move.get('Куда')
        if not dest:
            continue
        dest = dest.strip()
        qty = int(move.get('Количество', 0))
        if dest.lower() == 'склад':
            continue   # возвраты не попадают в локации, они обрабатываются отдельно
        else:
            if dest not in stock[eq]['locations']:
                stock[eq]['locations'][dest] = 0
            stock[eq]['locations'][dest] += qty
    return stock

@cached(cache)
def get_all_locations():
    """Возвращает словарь: {локация: {оборудование: количество}}"""
    sheet_mov = get_gsheet(SHEET_MOVEMENTS)
    moves = sheet_mov.get_all_records()
    locations = {}
    for move in moves:
        dest = move.get('Куда')
        if not dest or dest.lower() == 'склад':
            continue
        eq = move.get('Оборудование')
        qty = int(move.get('Количество', 0))
        if dest not in locations:
            locations[dest] = {}
        if eq not in locations[dest]:
            locations[dest][eq] = 0
        locations[dest][eq] += qty
    return locations

@cached(cache)
def get_equipment_names():
    sheet_eq = get_gsheet(SHEET_EQUIPMENT)
    records = sheet_eq.get_all_records()
    names = []
    for row in records:
        name = row.get('Название')
        if name:
            names.append(name)
    return names

def add_movement(equipment, destination, quantity, responsible='', comment=''):
    """Добавляет запись в Движения и очищает кеш"""
    sheet_mov = get_gsheet(SHEET_MOVEMENTS)
    now = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    sheet_mov.append_row([equipment, destination, quantity, now, responsible, comment])
    cache.clear()   # очищаем кеш, чтобы при следующем запросе данные обновились
    return True

# ---------- ДЕКОРАТОР АВТОРИЗАЦИИ ----------
def login_required(f):
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not session.get('logged_in'):
            return redirect(url_for('login'))
        return f(*args, **kwargs)
    return decorated_function

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        if request.form['password'] == ADMIN_PASSWORD:
            session['logged_in'] = True
            flash('Добро пожаловать!', 'success')
            return redirect(url_for('index'))
        else:
            flash('Неверный пароль', 'danger')
    return render_template('login.html')

@app.route('/logout')
def logout():
    session.pop('logged_in', None)
    flash('Вы вышли из системы', 'info')
    return redirect(url_for('login'))

# ---------- СТРАНИЦЫ ----------
@app.route('/')
@login_required
def index():
    stock = get_current_stock()
    for name, data in stock.items():
        issued = sum(data['locations'].values())
        data['stock'] = data['total'] - issued
    return render_template('index.html', stock=stock)

@app.route('/locations')
@login_required
def locations():
    locs = get_all_locations()
    return render_template('locations.html', locations=locs)

@app.route('/move', methods=['GET', 'POST'])
@login_required
def move():
    equipment_names = get_equipment_names()
    if request.method == 'POST':
        responsible = request.form.get('responsible', '')
        comment = request.form.get('comment', '')
        destination = request.form.get('destination', '').strip()
        if not destination:
            flash('Укажите локацию (куда отправляется оборудование)', 'danger')
            return redirect(request.url)
        
        items = request.form.getlist('equipment[]')
        quantities = request.form.getlist('quantity[]')
        errors = False
        for eq, qty_str in zip(items, quantities):
            if not eq or not qty_str:
                continue
            try:
                qty = int(qty_str)
                if qty <= 0:
                    flash(f'Количество для "{eq}" должно быть положительным', 'danger')
                    errors = True
                    continue
            except ValueError:
                flash(f'Некорректное количество для "{eq}"', 'danger')
                errors = True
                continue
            
            stock = get_current_stock()   # используем кешированную версию
            if eq not in stock:
                flash(f'Оборудование "{eq}" не найдено', 'danger')
                errors = True
                continue
            issued = sum(stock[eq]['locations'].values())
            available = stock[eq]['total'] - issued
            if available < qty:
                flash(f'Недостаточно "{eq}" (доступно: {available})', 'danger')
                errors = True
        
        if errors:
            return redirect(request.url)
        
        for eq, qty_str in zip(items, quantities):
            if eq and qty_str:
                qty = int(qty_str)
                add_movement(eq, destination, qty, responsible, comment)
        flash('Перемещение выполнено', 'success')
        return redirect(url_for('index'))
    
    return render_template('move.html', equipment_names=equipment_names)

@app.route('/receiving', methods=['GET', 'POST'])
@login_required
def receiving():
    if request.method == 'POST':
        equipment_list = request.form.getlist('equipment[]')
        location_list = request.form.getlist('location[]')
        quantity_list = request.form.getlist('quantity[]')
        responsible = request.form.get('responsible', '')
        comment = request.form.get('comment', '')
        
        for eq, loc, qty_str in zip(equipment_list, location_list, quantity_list):
            if not eq or not loc or not qty_str:
                continue
            try:
                qty = int(qty_str)
                if qty <= 0:
                    continue
            except ValueError:
                continue
            add_movement(eq, "Склад", qty, responsible, comment)
        flash('Возврат оформлен', 'success')
        return redirect(url_for('index'))
    
    # GET – собираем текущие выдачи (не возвраты) для отображения
    sheet_mov = get_gsheet(SHEET_MOVEMENTS)
    moves = sheet_mov.get_all_records()
    issued = {}
    for move in moves:
        eq = move.get('Оборудование')
        loc = move.get('Куда')
        if not loc or loc.lower() == 'склад':
            continue
        qty = int(move.get('Количество', 0))
        key = (eq, loc)
        issued[key] = issued.get(key, 0) + qty
    items = [{'equipment': eq, 'location': loc, 'quantity': qty} for (eq, loc), qty in issued.items()]
    return render_template('receiving.html', items=items)

if __name__ == '__main__':
    app.run(debug=True)