import gspread
from oauth2client.service_account import ServiceAccountCredentials

SPREADSHEET_ID = '1Nuk53a84vNg7I-I6heXDaZCOXS1Bs6-Pc-5mZYCDqIQ'  # ВАШ ПРАВИЛЬНЫЙ ID
SERVICE_ACCOUNT_FILE = 'credentials.json'
SCOPE = ['https://spreadsheets.google.com/feeds', 'https://www.googleapis.com/auth/drive']

try:
    creds = ServiceAccountCredentials.from_json_keyfile_name(SERVICE_ACCOUNT_FILE, SCOPE)
    client = gspread.authorize(creds)
    sheet = client.open_by_key(SPREADSHEET_ID)
    print("✅✅✅ УРА! Таблица найдена! Название:", sheet.title)
    print("Листы:", [ws.title for ws in sheet.worksheets()])
except Exception as e:
    print("❌ Ошибка:", e)
    if "404" in str(e):
        print("👉 Неверный ID или доступ. Проверьте:")
        print("   - В файле credentials.json скопируйте client_email и добавьте в таблицу как редактор.")
        print("   - Убедитесь, что в таблицу вставлен email: warehouse-bot@...")