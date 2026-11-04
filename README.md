# Roblox Auto Signup

## 1. What is this repo?

สคริปต์ Python สำหรับเปิดหน้าสมัคร Roblox กรอกฟอร์ม และบันทึกผลแต่ละบัญชี
พร้อม cookies, logs และปริมาณ bandwidth โดยเลือกวิธีรันได้ 3 แบบ:

| โหมด | การทำงาน |
| --- | --- |
| `terminal` | ใช้ Chromium แบบ headless ไม่แสดงหน้าต่าง แต่ยังโหลดเว็บและรัน JavaScript |
| `browser` (ค่าเริ่มต้น) | ทำงานเหมือน Terminal แต่เปิดหน้าต่าง Chromium/Chrome ให้เห็น |
| `http` | ส่ง request ด้วย Requests โดยตรง ไม่เปิด Browser และไม่รัน JavaScript |

เมื่อพบ CAPTCHA ใน Terminal/Browser โปรแกรมจะสุ่มเวลารอตรวจ login cookie
ตาม `CAPTCHA_BEFORE_CLOSE` หากพบ cookie จะบันทึก success; หากหมดเวลาจะบันทึก failed
โปรแกรมไม่ได้แก้ CAPTCHA อัตโนมัติ ส่วน HTTP จะ failed ทันทีเมื่อพบ CAPTCHA

หลังจบแต่ละบัญชีจะ export ผล ปิด Browser แล้วพักตามช่วง success/failed
ก่อนทำบัญชีถัดไป รอบสุดท้ายไม่พัก และ signup rate limit จะหยุดทั้ง run

ค่าเริ่มต้นบันทึกใน **`output/YYYY-MM-DD/`** ตามวันที่เริ่มรัน เวลาไทย
รันซ้ำวันเดียวกันจะสะสมข้อมูลในโฟลเดอร์เดิม โดยไม่ลบข้อมูลเก่า:

| ไฟล์หลัก | ข้อมูล |
| --- | --- |
| `userpass.txt`, `userfail.txt` | บัญชีที่สำเร็จ / ล้มเหลว |
| `cookies.txt` | Cookies ของบัญชีที่ยืนยันแล้ว |
| `results.json` | ผลรวมของแต่ละ attempt; `requested_count=0` หมายถึงมี run ที่ไม่จำกัดจำนวน |
| `process.log` | Log รายละเอียด |
| `bandwidth.jsonl` | ปริมาณข้อมูลและต้นทุนประมาณการต่อ attempt ไม่ใช่ยอดบิล proxy |

## 2. Parameters

**ตั้งค่าใน `.env`** — โปรแกรมอ่านให้อัตโนมัติ ตัวแปรที่กำหนดหน้าคำสั่งมีผลเหนือ `.env`
รวมถึงค่าว่าง ดูตัวอย่างค่าทั้งหมดใน [`.env.example`](.env.example)

| Parameter | ค่าเริ่มต้น | ความหมาย |
| --- | --- | --- |
| `ACCOUNT_COUNT` | `0` | จำนวน attempts เมื่อไม่ระบุจำนวนใน CLI; `0` = รันไม่จำกัด |
| `CAPTCHA_BEFORE_CLOSE` | `7,15` | สุ่มเวลารอตรวจ cookie หลังพบ CAPTCHA หน่วยวินาที ใช้เฉพาะ Terminal/Browser |
| `RANDOM_WHEN_DONE` | `30,45` | สุ่มเวลาพักหลัง success ก่อนบัญชีถัดไป |
| `RANDOM_WHEN_FAILED` | `60,100` | สุ่มเวลาพักหลัง failed ก่อนบัญชีถัดไป |
| `PROXIES` | ว่าง | ใส่ proxy โดยตรง คั่นหลายรายการด้วย comma |
| `PROXY_FILE` | ว่าง | ไฟล์ proxy หนึ่งรายการต่อบรรทัด เช่น `proxies.txt` |
| `PROXY_USERNAME`, `PROXY_PASSWORD` | ว่าง | ข้อมูลยืนยันตัวตนกลางสำหรับ proxy ที่ไม่ได้ใส่ credentials ไว้ในรายการ |
| `PASSWORD` | ว่าง = สุ่ม | รหัสผ่านที่จะใช้กับทุกบัญชี |
| `NAME_PREFIX` | ว่าง = สร้างชื่ออัตโนมัติ | ใช้ prefix สูงสุด 12 ตัวแล้วต่อเลข 6 หลัก; เติม `_` เฉพาะเมื่อยังไม่มี ห้าม prefix ขึ้นต้นด้วย `_` หรือมีเกินหนึ่งตัว |
| `SCRAMBLED_USERNAME` | `y` | สร้างชื่อแบบออกเสียง; `n` ใช้คำจากไฟล์ใน `lib/`; ทั้งสองแบบสุ่มเลข 0–3 ตัวและอาจมี `_` หนึ่งตัวภายในชื่อเมื่อไม่ได้ตั้ง prefix |
| `BIRTHDAY` | ว่าง = สุ่ม | วันเกิดรูปแบบ `YYYY-MM-DD` ต้องเป็นวันในอดีต |
| `BROWSER_PATH` | ค้นหาอัตโนมัติ | Path ของ Chromium/Chrome executable |
| `INCOGNITO` | `n` | `y` เปิด incognito; proxy ที่ต้องใช้ auth extension จะปิด incognito แต่ยังใช้ profile ใหม่ |
| `HTTP_USER_AGENT` | ค่าจากโค้ด | User-Agent สำหรับโหมด HTTP เท่านั้น |

ทุกชื่อผ่านตัวตรวจรูปแบบเดียวกัน: 3–20 ตัว ใช้ `A–Z`, `a–z`, `0–9` และ `_`
ได้ไม่เกินหนึ่งตัว โดย `_` ห้ามอยู่ต้นหรือท้าย ตาม [กฎ Roblox](https://en.help.roblox.com/hc/en-us/articles/4412614080532-Xbox-Error-901)
เช่น `River7Fox`, `River_Fox2`; `Demo_` เป็น prefix ได้ เพราะมีเลขต่อท้าย
เลขสุ่ม 0–3 ตัวเป็นรูปแบบของโปรเจกต์ ไม่ใช่ข้อจำกัดจำนวนเลขของ Roblox
การตรวจนี้ไม่ยืนยันว่าชื่อยังว่างหรือผ่านการกรองคำ/ข้อมูลส่วนบุคคลของ Roblox

ช่วงเวลาระบุเป็น `ต่ำสุด,สูงสุด` เช่น `7,15` สุ่มใหม่แต่ละครั้ง รองรับทศนิยม
และต้องเป็นตัวเลข finite ที่ `0 <= ต่ำสุด <= สูงสุด` ใช้ `15,15` เพื่อกำหนดเวลาคงที่
หรือ `0,0` เพื่อไม่รอ สำหรับ CAPTCHA จะยังตรวจ cookie หนึ่งครั้ง

ชื่อที่ถูกต้องคือ **`PROXY_FILE`** หากตั้งทั้ง `PROXIES` และ `PROXY_FILE`
โปรแกรมจะรวมรายการ ตัดรายการซ้ำ แล้วใช้วนตามลำดับบัญชี

**Parameters ตอนรัน** — ใช้ได้กับทั้ง `run.sh`, `run-terminal.sh` และ `run-browser.sh`
ยกเว้น `--mode` ให้ใช้กับ `run.sh` เพราะอีกสองสคริปต์เลือกโหมดไว้แล้ว

| Parameter | ค่าเริ่มต้น | ความหมาย |
| --- | --- | --- |
| `count` | `ACCOUNT_COUNT` หรือ `0` | `0` = รันไม่จำกัด; จำนวนบวก = จำนวน attempts รวม failed เช่น `./run.sh 5` |
| `--mode` | `browser` | เลือก `terminal`, `browser` หรือ `http` |
| `--export-mode` | `dated` | `dated` สะสมใน `output/YYYY-MM-DD/`; `append` สะสมใน `output/` |
| `--quiet` | ไม่เปิด | ซ่อน log รายละเอียดบนหน้าจอ แต่ยังมี bar, warning/error และ log ในไฟล์ |
| `--no-progress` | ไม่เปิด | ปิด loading bar ซึ่งปกติแสดงเมื่อรันใน interactive Terminal |
| `--browser-path` | `BROWSER_PATH` | ระบุ Browser executable แทนค่าใน `.env` |
| `--cost-per-gb` | `0` | ราคาต่อ GB สำหรับประมาณต้นทุน; `0` ไม่คำนวณ |
| `--request-timeout` | `60` วินาที | เวลาสูงสุดต่อ HTTP request หรือ Browser operation |
| `--connect-timeout` | `10` วินาที | เวลาเชื่อมต่อ สำหรับโหมด HTTP |
| `--read-timeout` | `30` วินาที | เวลารอข้อมูลระหว่างอ่าน response สำหรับโหมด HTTP |
| `--heartbeat` | `5` วินาที | ความถี่ log ระหว่างรอ ไม่ใช่เวลารอ CAPTCHA |
| `--help` | — | แสดงวิธีใช้โดยไม่เริ่มสมัคร |

เมื่อไม่ระบุจำนวน ทุกสคริปต์จะอ่าน `ACCOUNT_COUNT` จาก `.env` (ค่าเริ่มต้น `0`)
ค่า `0` หมายถึงรันไม่จำกัดจนกดหยุดหรือพบเงื่อนไขหยุด เช่น rate limit
หน้าจอแสดงจำนวนเป็น `1/∞`, `2/∞` และพักตามช่วง success/failed ก่อนทุกรอบถัดไป
จำนวนบวกคือจำนวน attempts ไม่ใช่จำนวนบัญชีที่รับประกันว่าจะสมัครสำเร็จ ค่าติดลบใช้ไม่ได้

## 3. Run script

**ติดตั้งครั้งแรก** บน macOS/Linux ต้องมี Python 3 และ Chromium/Chrome สำหรับ Terminal/Browser:

```sh
python3 -m venv venv
venv/bin/python -m pip install -r requirements.txt
chmod +x run.sh run-terminal.sh run-browser.sh
[ -f .env ] || cp .env.example .env
```

สคริปต์เลือก Python จาก `venv` → `.venv` → `python3` ตามลำดับ โดยไม่ต้อง activate

**รันด้วยค่าเริ่มต้น** — เปิด Browser ใช้ proxy และจำนวนตาม `.env`:

```sh
./run.sh
```

หาก `.env` ตั้ง `ACCOUNT_COUNT=0` จะรันไม่จำกัด กด `Ctrl+C` เพื่อหยุดและ export ผล
ถ้าต้องการบังคับจำนวนไม่จำกัดโดยไม่สนค่าใน `.env`:

```sh
./run.sh 0                  # Browser (ค่าเริ่มต้น)
./run-terminal.sh 0         # Headless
```

**รัน 1 attempt โดยไม่ใช้ proxy** เลือกคำสั่งตามโหมดที่ต้องการ:

```sh
# Terminal: ไม่แสดงหน้าต่าง Browser
PROXIES= PROXY_FILE= ./run-terminal.sh 1

# Browser: แสดงหน้าต่าง
PROXIES= PROXY_FILE= ./run-browser.sh 1

# HTTP: ส่ง request โดยตรง
PROXIES= PROXY_FILE= ./run.sh 1 --mode http
```

**ใช้ proxy จากไฟล์** ใส่หนึ่งรายการต่อบรรทัดใน `proxies.txt`:

```text
username:password@proxy.example.com:8080
```

ตัวอย่างรัน 5 attempts โดยใช้ไฟล์นี้ และแสดงเฉพาะ bar กับ warning/error:

```sh
PROXIES= PROXY_FILE=proxies.txt ./run-browser.sh 5 --quiet
```

**ตัวอย่างเพิ่มเติม** ใช้ proxy ตามที่ตั้งไว้ใน `.env`:

```sh
# สะสมผลไว้ใน output/ โดยไม่แยกวัน
./run-terminal.sh 1 --export-mode append

# ระบุ Browser หากค้นหาอัตโนมัติไม่พบ (ตัวอย่าง Chromium บน macOS)
./run-browser.sh 1 --browser-path /Applications/Chromium.app/Contents/MacOS/Chromium

# ดู options ทั้งหมด
./run.sh --help
```

กด **Ctrl+C** เพื่อหยุด โปรแกรมจะปิด Browser และ export ผลที่เก็บได้
รวมถึงเมื่อส่ง `SIGTERM`/`SIGHUP` หากหยุดระหว่างพักจะเก็บผลรอบก่อนหน้าไว้และไม่เริ่มรอบใหม่
`kill -9` ไม่สามารถทำ final export ได้ แต่ผลที่บันทึกไปแล้วจะยังอยู่
