from selenium import webdriver
from selenium.webdriver.chrome.options import Options
from bs4 import BeautifulSoup
import time
import json

# Configura il driver headless
options = Options()
options.add_argument('--headless')
options.add_argument('--disable-gpu')
options.add_argument('--no-sandbox')

# Inizializza Chrome
driver = webdriver.Chrome(options=options)

# Sito da analizzare
url = "https://www.garanteprivacy.it/"

driver.get(url)
time.sleep(5)  # Attendi caricamento

# Estrai i cookie
cookies = driver.get_cookies()
with open('cookies.json', 'w') as f:
    json.dump(cookies, f, indent=4)
print("✅ Cookie estratti e salvati in cookies.json")

# Estrai il sorgente HTML
html = driver.page_source

# Parsing con BeautifulSoup
soup = BeautifulSoup(html, "html.parser")

# Trova tutti i link contenenti 'privacy' nell'href o nel testo
privacy_links = []
for a in soup.find_all("a", href=True):
    href = a["href"].lower()
    text = a.get_text(strip=True).lower()
    if "privacy" in href or "privacy" in text:
        privacy_links.append(a["href"])

# Rimuovi duplicati
privacy_links = list(set(privacy_links))

print("\n🔍 Link trovati relativi alla privacy:")
for link in privacy_links:
    print(link)

driver.quit()
