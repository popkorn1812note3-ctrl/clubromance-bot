# Деплой ClubRomance на VPS

Два варианта. Начни с **А (polling)** — он работает сразу, без домена. На webhook (Б)
перейдёшь позже, когда будет домен.

Предполагается Ubuntu/Debian. Подключайся через Termius по SSH.

---

## 0. Подготовка сервера (один раз)

```bash
sudo apt update && sudo apt install -y python3 python3-venv python3-pip git
python3 --version          # нужен 3.11+ (на проде разрабатывалось на 3.13)
```

Отдельный пользователь и папка:

```bash
sudo useradd -r -m -d /opt/clubromance clubromance      # сервисный пользователь
sudo -u clubromance -s                                  # зайти под ним
cd /opt/clubromance
```

## 1. Забрать код с GitHub

```bash
git clone https://github.com/popkorn1812note3-ctrl/clubromance-bot.git .
# (репозиторий приватный — git спросит логин и Personal Access Token вместо пароля)
```

## 2. Окружение и зависимости

```bash
python3 -m venv .venv
.venv/bin/pip install --upgrade pip
.venv/bin/pip install -r requirements.txt
```

## 3. Конфиг `.env`

```bash
cp .env.example .env
nano .env
```

Заполни как минимум:

```
MAX_BOT_TOKEN=<токен из @MasterBot>
BOT_USERNAME=id744843005727_2_bot
ADMIN_IDS=5479775,3958992          # твои id для команды /give
```

Проверка связи (должно вывести имя бота):

```bash
.venv/bin/python -c "import asyncio; from app.config import load_config; from app.max_client import MaxClient; \
print(asyncio.run(MaxClient(load_config().token).get_me()))"
```

---

## Вариант А — POLLING (быстрый старт, без домена)

```bash
exit                                                    # выйти из-под clubromance к sudo-юзеру
sudo cp /opt/clubromance/deploy/clubromance.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clubromance
sudo systemctl status clubromance --no-pager            # active (running)?
journalctl -u clubromance -f                            # живые логи
```

Бот сразу принимает сообщения. systemd сам перезапустит его при падении/перезагрузке.

Обновление кода в будущем:

```bash
sudo -u clubromance git -C /opt/clubromance pull
sudo -u clubromance /opt/clubromance/.venv/bin/pip install -r /opt/clubromance/requirements.txt
sudo systemctl restart clubromance
```

---

## Вариант Б — WEBHOOK (прод, нужен домен + HTTPS)

Когда появится домен (напр. `bot.example.com`, A-запись на IP сервера):

1. В `.env` добавь:
   ```
   WEBHOOK_BASE_URL=https://bot.example.com
   WEBHOOK_SECRET=<любая случайная строка, напр. openssl rand -hex 16>
   ```
2. nginx + сертификат:
   ```bash
   sudo apt install -y nginx certbot python3-certbot-nginx
   sudo cp /opt/clubromance/deploy/nginx.conf.example /etc/nginx/sites-available/clubromance
   sudo nano /etc/nginx/sites-available/clubromance        # заменить bot.example.com
   sudo ln -s /etc/nginx/sites-available/clubromance /etc/nginx/sites-enabled/
   sudo certbot --nginx -d bot.example.com
   sudo nginx -t && sudo systemctl reload nginx
   ```
3. Переключить сервис на webhook:
   ```bash
   sudo systemctl disable --now clubromance               # выключить polling
   sudo cp /opt/clubromance/deploy/clubromance-webhook.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now clubromance-webhook
   journalctl -u clubromance-webhook -f                    # «Webhook зарегистрирован: ...»
   ```

Бот при старте сам зарегистрирует подписку `https://bot.example.com/webhook/<secret>`
и снесёт чужие webhook'и.

---

## Перенос прогресса игроков (если нужно)

База — один файл SQLite `clubromance.db`. Чтобы перенести с локальной машины:

```bash
# на сервере останови сервис, скопируй файл (scp/Termius), запусти снова
sudo systemctl stop clubromance
# залей clubromance.db в /opt/clubromance/ (chown clubromance:clubromance)
sudo systemctl start clubromance
```

При росте нагрузки слой БД (`app/db.py`) можно перевести на Postgres — отдельной итерацией.

---

## Веб-админка (картинки сцен + каналы ОП)

Отдельный сервис на порту 8080, делит БД с ботом.

```bash
# зависимость для загрузки файлов (если ещё не стоит)
sudo -u clubromance /opt/clubromance/.venv/bin/pip install -q python-multipart

# логин/пароль админки в .env
sudo -u clubromance nano /opt/clubromance/.env
#   ADMIN_USER=admin
#   ADMIN_PASSWORD=<надёжный пароль>
#   ADMIN_IDS=<твой max id>,<второй>   # байпас подписки + /give

# сервис
sudo cp /opt/clubromance/deploy/clubromance-admin.service /etc/systemd/system/
sudo systemctl daemon-reload
sudo systemctl enable --now clubromance-admin
journalctl -u clubromance-admin -n 10 --no-pager

# открыть порт
sudo ufw allow 8080/tcp     # если ufw активен
```

Заходи: `http://<IP-сервера>:8080` (логин/пароль из `.env`). Там: история → загрузка
фонов по локациям и картинок к ключевым сценам; раздел каналов обязательной подписки.

> Безопаснее без открытого порта — SSH-туннель: `ADMIN_HOST=127.0.0.1`, в Termius
> проброс порта 8080, заходи на `http://localhost:8080`.

## Шпаргалка

| Действие | Команда |
|---|---|
| Логи | `journalctl -u clubromance -f` |
| Рестарт | `sudo systemctl restart clubromance` |
| Статус | `sudo systemctl status clubromance` |
| Стоп | `sudo systemctl stop clubromance` |
| Выдать себе кристаллы | написать боту `/give 1000` |
