# Crypto Skills

ภาษา: [English](README.md) · ไทย

**Skill วิเคราะห์คริปโตที่ยึดหลักฐานเป็นหลัก พร้อม AI Portfolio Trading OS ที่รันบนเครื่องตัวเองในโหมด PAPER เท่านั้น**

![PAPER only](https://img.shields.io/badge/execution-PAPER%20only-f5b301) ![Real-money orders](https://img.shields.io/badge/real--money%20orders-blocked%20by%20design-7c3aed) ![Python](https://img.shields.io/badge/python-3.11%2B%20stdlib%20only-3776ab) ![Frontend](https://img.shields.io/badge/frontend-vanilla%20ES%20modules-222) ![Status](https://img.shields.io/badge/status-feature%20freeze%20%C2%B7%20PAPER%20campaign%20next-2f6fed)

![หน้า Overview](docs/images/overview.png)

repository นี้มี 2 ส่วน:

1. **`crypto-market-trading-analysis`**: skill สำหรับ Codex / Claude / Gemini ใช้วิเคราะห์ตลาดจากหลักฐาน วางแผนการเข้าออก และตัดสินใจโดยคำนึงถึงความเสี่ยง
2. **แล็บเทรด PAPER บนเครื่อง**: cockpit สำหรับพอร์ต Spot + Perpetual ที่ใช้ข้อมูลตลาดสาธารณะจริงของ Gate ใช้ Jev แบบ typed decision และส่งต่อให้ GPT-6 Luna เมื่อจำเป็น (ไม่บังคับ) มี risk และ execution safety แบบ deterministic มีการจัดการ Spot รอบใหญ่ กู้คืนระบบได้เมื่อ crash และมี gate สำหรับเลื่อนขั้นการทดลอง

> [!IMPORTANT]
> **ไม่มีการใช้เงินจริง** ทุก fill เป็นการจำลอง เส้นทางส่งคำสั่ง แก้คำสั่ง ยกเลิกคำสั่ง เปลี่ยน leverage โอน และถอนบน Gate ถูกบล็อกโดยการออกแบบ server ผูกกับ loopback เท่านั้น และ credential ไม่ถูกเก็บใน SQLite, prompt, log, API response หรือไฟล์ export

## เริ่มใช้งาน

```bash
git clone https://github.com/myh-st/crypto-skills.git && cd crypto-skills
python3 -m crypto_eval paper-server            # http://127.0.0.1:8765/  (ครั้งแรกใช้ข้อมูล fixture)
```

ไม่ต้องติดตั้งอะไรเพิ่ม Python ใช้แค่ standard library และ frontend ไม่มีขั้นตอน build

- **ข้อมูลตลาดจริง:** เปลี่ยน experiment เป็น `gate_usdt` ที่ *Research › Paper Trading Lab* (ใช้ endpoint สาธารณะของ Gate ไม่ต้องล็อกอิน)
- **AI จริง (ไม่บังคับ):** ใส่ credential ใน `.env` แล้วรัน `python3 -m crypto_eval paper-setup-real` คำสั่งนี้ย้าย credential เข้า OS credential store และตั้งค่า Jev กับ Azure AI Foundry จากนั้นกดทดสอบ provider แต่ละตัวในหน้า Settings

## ทำอะไรได้บ้าง

| | |
|---|---|
| **Automation, kill switch และ system health:** หยุดหรือพักระบบอัตโนมัติได้ ยก kill switch ได้ทันที แต่การลดระดับต้องยืนยันและ reconciliation ต้องผ่าน health แสดงสถานะ scheduler, monitor, feed, ฐานข้อมูล, พื้นที่ดิสก์, AI provider, งบ AI และ reconciliation | ![Automation และ health](docs/images/automation-health.png) |
| **Trade:** กราฟ Gate แบบ live (1m ถึง 4h) พร้อมจุดเข้าออก PAPER, quote, spread และ funding ticket ให้ server คำนวณขนาดเอง (Perp คิดขนาดจาก risk และ stop ไม่ต้องพิมพ์จำนวน) และแผน AI ปัจจุบันที่สั่ง re-plan ได้ในคลิกเดียว | ![Trade](docs/images/trade.png) |
| **จัดการ position:** แก้ stop/เป้า ลดหรือปิด position และกำหนดอำนาจ (`AUTO_PAPER`, `RECOMMEND_ONLY`, `MANUAL_OVERRIDE`, `PAUSED`) แสดงสถานะความปลอดภัยแบบ live, สถานะ lifecycle ของ Spot และสัดส่วน Core/Tactical | ![Position manager](docs/images/position-manager.png) |
| **Portfolio:** รวม Spot และ Perpetual ในมุมมองการจัดสรรเดียว มี exposure รายสินทรัพย์ กราฟ equity ตัวเลขเศรษฐศาสตร์ และคำสั่งซื้อขาย ส่วน mirror ของบัญชีจริงแยกไว้และอ่านได้อย่างเดียว | ![Portfolio](docs/images/portfolio.png) |
| **Activity:** บันทึกรวมทุกอย่างที่ AI, คุณ และระบบทำ ทั้ง order, fill, lifecycle review, safety event และ alert | ![Activity](docs/images/activity.png) |
| **Promotion gate:** manifest ของการทดลองถูกตรึงไว้ มี checkpoint วันที่ 7/30/60/90 ที่สร้างซ้ำได้ และคำตัดสินแบบ deterministic ถึงได้ PASS ก็ไม่เปิดการเทรดเงินจริง | ![Promotion gate](docs/images/promotion-gate.png) |
| **Spot lifecycle benchmark:** เทียบการจัดการ lifecycle กับ Buy & Hold, TP ladder, rebalance, grid และ trailing stop บนแท่งเทียน ค่าธรรมเนียม และ slippage ชุดเดียวกัน | ![Lifecycle benchmark](docs/images/lifecycle-benchmark.png) |

## ความสามารถ

| ด้าน | ทำอะไร | เอกสาร |
|---|---|---|
| **Portfolio OS** | catalog Spot + Perp ของ Gate; บัญชี Spot แยก (ต้นทุนเฉลี่ย ค่าธรรมเนียม limit ที่ fill บางส่วน); order รวมศูนย์ที่ใช้ `client_request_id` กันส่งซ้ำ; โหมดอำนาจ; AI re-plan แบบมีโครงสร้างพร้อม diff ก่อน/หลัง; Portfolio Brain ที่ทำได้แค่ลดขนาดหรือบล็อก; คิวเรื่องที่ต้องดู; การเรียนรู้หลังปิดเทรด; tournament ที่รวมต้นทุน AI | [ai-portfolio-trading-os.md](docs/ai-portfolio-trading-os.md) |
| **AI decision stack** | แท่ง 15m ที่ปิดแล้ว → features และ quant gate แบบ deterministic → Jev typed decision → escalation policy แบบมีเวอร์ชัน → GPT-6 Luna พร้อม skill นี้ (ไม่บังคับ) → intent ที่ผ่านการ validate AI กำหนดขนาดหรือ leverage เองไม่ได้ และข้าม risk ไม่ได้ มี budget guard พร้อม price book แบบมีเวอร์ชัน และ paid call จะ fail closed | [paper-futures-runtime.md](docs/paper-futures-runtime.md) |
| **Trend sleeves engine** | EXP-002 (`sleeves_v1`) มี 3 กลยุทธ์ futures ที่เปิดได้ทั้ง long และ short แต่ละกลยุทธ์ใช้บัญชีย่อย PAPER ของตัวเอง ได้แก่ Donchian 4h breakout พร้อม trailing stop, time-series momentum 60 วัน และ cross-sectional momentum รายสัปดาห์ บน BTC, ETH, NEAR, SEI, SUI, AVAX และ ENA และปรับสัดส่วนทุนกลับเท่ากันทุกเดือน ผล replay 4 ปีผ่าน runtime จริงได้ Sharpe 1.32 (+37% ต่อปี) และ max drawdown 18.5% เมื่อคิดต้นทุน 2 เท่าได้ Sharpe 1.18 | [trend-sleeves-engine.md](docs/trend-sleeves-engine.md) |
| **Execution safety** | สถานะตลาด (`NORMAL`, `VOLATILITY_ALERT`, `CRASH_MODE`, `RECOVERY`, `MARKET_DATA_UNTRUSTED`); กรอง print ผิดปกติ; execution planner (กรอบ slippage, แบ่งไม้, TTL, จำกัดความเร็วการขาย); kill switch 5 ระดับ; reconcile บัญชี; ลด position ฉุกเฉินเมื่อใกล้ liquidation; กันคำสั่งซ้ำ คำสั่งหมดอายุ และคำสั่งผิดฝั่ง | [crash-execution-safety.md](docs/crash-execution-safety.md) |
| **Spot lifecycle** | สถานะรอบใหญ่แบบมีชนิด (สะสม → ถือ Core → เทรนด์ขยาย → ป้องกันกำไร → ทยอยขาย → ลด → ออก → ถือเงินสด); หลักฐาน regime แบบ point-in-time; ทยอยขายแทนขายหมดทีเดียว; ขาย Core เฉพาะเมื่อยืนยันการพังของโครงสร้าง; benchmark เทียบในเงื่อนไขเดียวกัน | [spot-cycle-lifecycle-manager.md](docs/spot-cycle-lifecycle-manager.md) |
| **Resilience** | กู้สถานะตอนเริ่มก่อนระบบอัตโนมัติทำงาน; บันทึกรอบ scheduler (ช่วงที่พลาดบันทึกชัดเจน ไม่เติมข้อมูลย้อนหลัง); incident และ health; circuit breaker ของ provider; backup/restore ที่ตรวจสอบแล้วและไม่มี secret; ล็อกให้รันได้ instance เดียว; ปิดระบบอย่างเรียบร้อยเมื่อได้ SIGTERM; soak แบบเร่งเวลา | [continuous-paper-resilience.md](docs/continuous-paper-resilience.md) |
| **Promotion gates** | manifest การทดลองถูกตรึง (เปลี่ยนสาระสำคัญต้องสร้างเวอร์ชันใหม่ ถ้าเปลี่ยนโดยไม่บันทึก รีวิวจะเป็น invalid); รายงาน checkpoint พร้อมตัวหาร; คำตัดสิน `PASS` / `CONTINUE_COLLECTING_DATA` / `FAIL_*` / `INVALID_EXPERIMENT` | [experiment-promotion-gates.md](docs/experiment-promotion-gates.md) |
| **Analysis skill** | evidence ledger → Bull vs Bear → judge → แผนเข้าออก → มุมมองความเสี่ยง → การตัดสินใจ ปลอดภัยแบบ point-in-time และตอบสั้นโดยให้คำตัดสินก่อน | [SKILL.md](skills/crypto-market-trading-analysis/SKILL.md) |

## เส้นทางการตัดสินใจ PAPER

```text
ข้อมูลสาธารณะ Gate (REST + WebSocket)  ─►  แท่ง 15m ที่ปิดแล้ว + บริบท 1h/4h
        │
        ▼
features + quant gate ─► Jev typed decision ─► escalation policy ─► (ถ้าเปิด) GPT-6 Luna + skill
        │                                                                   │
        └──────────────────────────► TradingIntent ที่ validate แล้ว ◄──────┘
                                          │
          RiskEngine คำนวณขนาด ─► Portfolio Brain (ลด/บล็อก) ─► crash & execution safety
                                          │
                     PAPER fills · funding · liquidation · บัญชี Spot
                                          │
        SQLite ledger ─► reconciliation ─► activity / attention ─► checkpoint & promotion gate
```

ลำดับอำนาจ: **liquidation/บัญชี > risk engine > crash/price/liquidity guard > Portfolio Brain > AI/มนุษย์** ชั้นที่ต่ำกว่าลดขนาด เลื่อน หรือบล็อกได้ แต่ขยายสิ่งที่ชั้นบนอนุญาตไม่ได้

## สถานะโปรเจกต์

| Phase | ขอบเขต | สถานะ |
|---|---|---|
| 1 | AI Portfolio Trading OS | ✅ merged |
| 2 | Crash, price, liquidity และ execution safety | ✅ merged |
| 3 | Spot Cycle Lifecycle Manager | ✅ merged |
| 4 | Continuous PAPER resilience | ✅ merged |
| 5 | Experiment promotion gates | ✅ merged |
| 6 | Live execution gateway | ⛔ วางแผนเท่านั้น ปิดการเทรดจริง |

**Strategy engine:** EXP-001 ใช้ 15m breakout ที่มี AI routing ส่วน EXP-002 ใช้ [trend sleeves engine](docs/trend-sleeves-engine.md) ทั้งสองตัวเป็น PAPER เท่านั้น

**หยุดพัฒนาฟีเจอร์ใหม่แล้ว ขั้นต่อไปคือแคมเปญ PAPER:** ทุน PAPER 500 USDT, checkpoint วันที่ 7/30/60/90 และเป้าเทรดที่ปิดแล้ว 200–300 ครั้ง ดู [runbook แคมเปญ](docs/paper-500-campaign-runbook.md) และ [development train](docs/development-train.md)

## คำสั่งที่ใช้บ่อย

```bash
python3 -m crypto_eval paper-server [--database P] [--no-live-stream]   # แอป (loopback เท่านั้น)
python3 -m crypto_eval paper-setup-real                                  # ย้าย credential จาก .env เข้า OS credential store
python3 -m crypto_eval paper-checkpoint [--database P] [--dry-run]       # รายงาน checkpoint + promotion gate
python3 -m crypto_eval paper-backup [--database P]                       # snapshot ที่ตรวจสอบแล้วและไม่มี secret
python3 -m crypto_eval paper-restore BACKUP [--database P] [--force]     # กู้คืน (ต้องหยุด server ก่อน)
python3 -m crypto_eval paper-soak --database FRESH.sqlite3 --days 3      # soak แบบเร่งเวลา (restart/sleep)
python3 -m crypto_eval portfolio-real-check [--full-loop]                # ทดสอบ local กับของจริง (มีค่าใช้จ่าย AI)
python3 -m crypto_eval demo --out-dir reports/crypto-eval-demo           # demo evaluation harness แบบ offline
```

## การทดสอบ

```bash
python3 scripts/validate_repo.py                  # ตรวจ schema, ตัวอย่าง, enum และโครงสร้าง skill
python3 -m unittest discover -s tests -v          # เทสต์ deterministic 300+ ตัว (ใช้ fixture/fake เท่านั้น)
node --test frontend/tests/*.test.mjs             # เทสต์การ render ของ frontend
find frontend -name '*.js' -exec node --check {} \;
```

CI (`.github/workflows/validate.yml`) รันทั้งหมดข้างบนพร้อม demo ในทุก PR และไม่เรียก network หรือ API ที่มีค่าใช้จ่าย ข้อมูล Gate จริงและ Jev/Luna จริงจะถูกใช้เฉพาะคำสั่ง local acceptance ที่เรียกเองเท่านั้น

---

## Analysis skill

### ติดตั้ง

```bash
mkdir -p ~/.codex/skills
ln -sfn "$PWD/skills/crypto-market-trading-analysis" ~/.codex/skills/crypto-market-trading-analysis
```

Claude Code ใช้ `~/.claude/skills/` ส่วน Gemini CLI ใช้ `gemini skills link <path>` repository นี้ไม่มี credential ใด ๆ ให้เชื่อมต่อแหล่งข้อมูลตลาดผ่าน runtime environment ของคุณ

<details>
<summary><b>Prompt ติดตั้งแบบครั้งเดียว</b> (Codex, Claude Code, Gemini CLI หรือ agent อื่น): ตรวจ client ติดตั้งอย่างปลอดภัย validate และรายงานผล</summary>

```text
คุณกำลังติดตั้ง repository Crypto Skills เป็น skill สำหรับวิเคราะห์ตลาดคริปโตที่ใช้ซ้ำได้

แหล่งอ้างอิงหลัก (source of truth):
  https://github.com/myh-st/crypto-skills.git
โฟลเดอร์ skill ภายใน repository:
  skills/crypto-market-trading-analysis

เป้าหมาย:
  ตรวจว่า AI client ปัจจุบันคือ Codex, Claude Code, Gemini CLI หรือ agent อื่น
  แล้วติดตั้ง/อัปเดต skill ใน scope ที่รองรับและปลอดภัยที่สุด จากนั้น validate และ
  รายงานสิ่งที่เปลี่ยนแปลงแบบกระชับ ให้ใช้ workspace เป็นค่าเริ่มต้นสำหรับการติดตั้ง
  เฉพาะโปรเจกต์ ใช้ user/global scope เมื่อผู้ใช้ขอโดยตรง หรือ client ไม่มีโฟลเดอร์
  skill ระดับ workspace

กฎความปลอดภัย:
1. ตรวจ OS, working directory, client/version และสถานะ repository ก่อนแก้ไฟล์
   ห้ามใช้ `git reset --hard`, การลบ recursive แบบกว้าง หรือคำสั่งที่เขียนทับไฟล์
   อื่นโดยไม่เกี่ยวข้อง
2. ถ้ายังไม่มี repository ให้ clone ไปยัง directory ที่รายงานได้ชัดเจน ถ้ามีอยู่แล้ว
   ให้ fetch/update เฉพาะเมื่อเป็น repository เดียวกัน และเก็บ uncommitted work ไว้
   บันทึก commit SHA ที่ติดตั้งจริง
3. ต้องติดตั้งทั้งโฟลเดอร์ skill ที่มี `SKILL.md` เพื่อให้ `references/`, `examples/`
   และ metadata ใต้ `agents/` ยังใช้งานได้
4. ถ้าปลายทางมีอยู่แล้ว ให้เปรียบเทียบกับ source ก่อน ใช้ symlink สำหรับ checkout
   ที่กำลังพัฒนา หรือ copy สำหรับ portable install ก่อนแทนที่ directory ที่ไม่ใช่
   source ให้ย้าย directory นั้นไป backup ที่มี timestamp และรายงาน path ให้ผู้ใช้
   ขออนุญาตก่อนการแทนที่ที่ทำลายข้อมูลหรือก่อนใช้สิทธิ์ยกระดับ
5. รัน validator ของ repository: `python3 scripts/validate_repo.py` ตรวจว่า
   `SKILL.md` เริ่มด้วย YAML frontmatter ที่ถูกต้อง และเมื่อติดตั้งสำหรับ Codex
   ต้องมี `agents/openai.yaml` ด้วย ถ้ามี Codex validator ให้รัน
   `uv run --with pyyaml python ~/.codex/skills/.system/skill-creator/scripts/quick_validate.py skills/crypto-market-trading-analysis` โดยไม่ติดตั้ง package แบบ global

ใช้ integration ตาม client ที่ตรวจพบ:
- Codex: user scope ใช้ `~/.codex/skills/crypto-market-trading-analysis` หรือ
  workspace scope ใช้ `.codex/skills/crypto-market-trading-analysis` แนะนำ symlink
  ไปยัง checkout ระหว่างพัฒนา ถ้าต้อง reload ให้เปิด session ใหม่หรือใช้วิธี reload
  ของ client แล้วตรวจว่าเรียก `$crypto-market-trading-analysis` ได้
- Claude Code: personal scope ใช้ `~/.claude/skills/crypto-market-trading-analysis`
  หรือ project scope ใช้ `.claude/skills/crypto-market-trading-analysis` ตรวจด้วย
  `/skills` และหลังสร้าง top-level skills directory ใหม่ให้ใช้ `/reload-skills`
  เมื่อ client รองรับ
- Gemini CLI: ถ้ามีคำสั่ง `gemini skills` ให้ติดตั้ง subdirectory
  `skills/crypto-market-trading-analysis` (ใช้ repository URL ตรง ๆ เฉพาะเมื่อ client
  รองรับ monorepo subdirectory) หรือ clone ไว้ก่อนแล้วใช้
  `gemini skills link <local-skill-path>` ใช้ `--scope workspace` เฉพาะเมื่อต้องการ
  ติดตั้งในโปรเจกต์ ถ้าไม่มี manager ให้ใช้ `~/.gemini/skills/` หรือ `.gemini/skills/`
  (รองรับ alias `.agents/skills/` ด้วย) แล้วตรวจ `/skills list` และ refresh ด้วย
  `/skills reload`
- AI Agent อื่น: ตรวจ native skill directory ตามเอกสารของ client แล้ววาง skill ไว้ที่นั่น
  ถ้าไม่มี skill manager ให้เก็บ checkout ไว้และโหลด
  `skills/crypto-market-trading-analysis/SKILL.md` เป็น instruction โดยตรง พร้อม
  รายงานว่าเป็น explicit-reference installation ไม่ใช่ native discovery ห้ามรายงานว่าสำเร็จ
  โดยไม่แสดง path จริง

ความปลอดภัยของ CoinMarketCap / MCP:
- ห้ามพิมพ์, commit, embed หรือใส่ API key ไว้ใน prompt, README, shell history,
  log, screenshot หรือไฟล์ที่ generate หากผู้ใช้อนุญาต key แล้ว ให้เก็บเป็น
  `CMC_API_KEY` ใน environment หรือ secret manager ที่ host รองรับ และตรวจด้วย metadata
  request แบบ authenticated ที่ไม่เปิดเผยค่า key
- ถ้ามี CoinMarketCap MCP server อยู่แล้ว ให้ตั้งค่าผ่าน MCP settings ของ client และ
  รายงานชื่อ server กับ read-only tools ถ้าไม่มี อย่าสร้างชื่อ package เองหรือแอบติดตั้ง
  ให้รายงานว่า adapter ยังขาด และปล่อยให้ skill ใช้ data source อื่นได้
- skill นี้เป็น read-only ห้ามส่ง order, โอนเงิน, ขอ private key หรือเปิดสิทธิ์ custody/trading

รายงานเมื่อเสร็จ (ต้องมี):
- client และ install scope ที่ตรวจพบ
- checkout ต้นทางและ commit SHA
- path ที่ติดตั้งจริงและระบุว่าเป็น symlink หรือ copy
- คำสั่ง validate และผล pass/fail
- คำสั่ง reload/restart และตัวอย่าง invocation สั้น ๆ
- สถานะ CMC/MCP โดยไม่เปิดเผย secret
- warning, permission ที่ขาด หรือ native integration ที่ไม่รองรับ
```

เอกสารอ้างอิงตาม client: [Claude Code Skills](https://code.claude.com/docs/en/skills) · [Gemini CLI Agent Skills](https://github.com/google-gemini/gemini-cli/blob/main/docs/cli/using-agent-skills.md)

</details>

### วิธีเรียกใช้

```text
$crypto-market-trading-analysis วิเคราะห์ SEI/USDT แบบ spot swing พร้อม buy zone, invalidation, targets และ risk
```

คำตอบเริ่มต้นจะสั้นและให้คำตัดสินก่อน:

1. สถานะการตัดสินใจ;
2. โซนเข้าหลักและโซนสำรอง;
3. จุด invalidation;
4. เป้าหมายและกรอบเวลา;
5. เหตุผลชี้ขาด 3–5 ข้อ;
6. ความเสี่ยงสำคัญหนึ่งข้อ หรือสิ่งที่จะทำให้เปลี่ยนมุมมอง

ถ้าขอรายงานละเอียดจะเห็น evidence ledger, scenario map และ decision object คำศัพท์สถานะการตัดสินใจสุดท้ายนิยามไว้ที่ [`schemas/decision-state.schema.json`](schemas/decision-state.schema.json) ที่เดียว

<details>
<summary><b>หลักการออกแบบ</b></summary>

- แยกข้อเท็จจริงออกจากการตีความด้วย evidence ledger
- ให้โครงสร้างราคาและการมีส่วนร่วมของ spot เป็นหลัก ส่วน derivatives ใช้อธิบายความเปราะบาง
- ให้ Bull และ Bear โต้แย้งบนหลักฐานชุดเดียวกัน แทนที่จะเล่าเรื่องแข่งกัน
- แยกทิศทางออกจากจังหวะ: สินทรัพย์ขาขึ้นก็ยังอาจเป็น `WAIT_FOR_PULLBACK` ได้
- กำหนดจุดเข้า invalidation และเป้าหมายจากระดับราคาและความผันผวนที่สังเกตได้จริง
- ใช้ข้อมูลแบบ point-in-time กับการวิเคราะห์ย้อนหลัง: ห้ามมีข้อมูลใดหลัง `data_cutoff`
- ข้อมูลที่ไม่มีต้องระบุว่าไม่มี ห้ามเติมเป็นศูนย์

```text
Market / spot / derivatives / options / on-chain / tokenomics / macro
        → normalize + timestamp + quality-check → neutral evidence ledger
        → Bull thesis vs Bear thesis → research judge → execution planner
        → aggressive / neutral / conservative risk lenses → portfolio decision
        → concise answer + monitoring conditions + journal record
```

[Interactive architecture diagram](docs/architecture.html)

![Crypto Skills evidence-to-decision architecture](docs/architecture-preview.png)

</details>

<details>
<summary><b>Evaluation harness</b>: การ validate ไม่ใช่หลักฐานความแม่นยำ</summary>

#### Validation != Accuracy Evaluation

`validate_repo.py` และ unit test ตรวจแค่โครงสร้างและพฤติกรรมแบบ deterministic **ไม่ได้**พิสูจน์ว่า skill แม่นยำหรือทำกำไรได้

```bash
python3 -m crypto_eval demo --out-dir reports/crypto-eval-demo
```

demo จะสร้าง dataset แบบ point-in-time, ตรึง prediction จาก fixture, ให้คะแนนจาก outcome ที่แยกเก็บ และเทียบกับ baseline คงที่ (Buy & Hold, BTC, EMA20/50, RSI14, naive, seeded random) ให้อ่านผลลัพธ์เป็น **DEMO / HARNESS VALIDATION, NOT MARKET PERFORMANCE EVIDENCE**

`eval/specs/crypto-market-v1.json` กำหนดเป้าหมายแบบเรียงตามเวลาและไม่สลับลำดับ ครอบคลุม BTC, ETH, SOL, SUI, SEI, AVAX และ PYTH prediction ถูกตรึงก่อนรู้ outcome และ wait ที่ trigger ไม่เคยเกิดจะไม่ถูกนับเป็นการเข้าที่ล้มเหลว ดู [`docs/evaluation.md`](docs/evaluation.md)

</details>

<details>
<summary><b>โครงสร้าง repository</b></summary>

```text
crypto-skills/
├── skills/crypto-market-trading-analysis/   # SKILL.md, references/, examples/, agents/
├── crypto_eval/                              # harness + PAPER runtime (stdlib only)
│   ├── paper_runtime.py  paper_server.py     # scheduler, risk engine, store, HTTP API
│   ├── paper_ai.py  ai_cost.py               # Jev / Luna adapters, cost ledger, budget guard
│   ├── market_catalog.py  gate_*.py          # Gate catalog, REST, WebSocket, read-only account
│   ├── portfolio_os.py  portfolio_brain.py   # orders, positions, authority, re-plan, brain
│   ├── execution_safety.py                   # market states, planner, kill switch, reconciliation
│   ├── spot_lifecycle.py  spot_benchmarks.py # lifecycle policy และ benchmark arms
│   ├── resilience.py  soak.py                # recovery, incidents, backup/restore, soak
│   └── promotion.py                          # manifests, checkpoints, promotion gate
├── frontend/                                 # cockpit แบบ vanilla ES modules (ไม่มี build step)
├── schemas/                                  # JSON Schema contracts แบบมีเวอร์ชัน
├── examples/                                 # analysis-output.yaml, decision-record.yaml, evidence-ledger.yaml
├── docs/                                     # เอกสารออกแบบ, runbook, ภาพหน้าจอ (docs/images)
├── tests/                                    # Python tests แบบ deterministic (fixture/fake เท่านั้น)
└── scripts/validate_repo.py                  # ตรวจโครงสร้างโดยไม่พึ่ง dependency
```

</details>

## ขอบเขตข้อมูลและความปลอดภัย

- เป็น PAPER เท่านั้น การเขียนคำสั่งที่เคลื่อนย้ายเงินจริงบน Gate ถูกบล็อกในระดับโค้ด (`DisabledLiveExecutionAdapter`)
- server ผูกกับ loopback และ `paper-server` ปฏิเสธ host ที่ไม่ใช่ loopback
- credential มาจาก environment ของ process, `.env` ของ repo (อ่านแบบ key=value ไม่ execute) หรือ OS credential store เท่านั้น browser ไม่เคยได้รับค่า secret
- ทุกข้อมูลมี timestamp พร้อม venue และ instrument ข้อมูลที่ขาดหรือเก่าจะถูกระบุว่าไม่มี และ feed ที่เก่าจะบล็อกการเข้าใหม่
- backup จะถูกปฏิเสธถ้าพบค่าที่มีลักษณะเป็น credential

## แนวทาง contribution

ระหว่างแคมเปญ PAPER รับเฉพาะงานแก้ข้อบกพร่องด้าน correctness, safety, reliability, observability และ methodology (ดู [stop condition](docs/development-train.md))

- เก็บ domain guidance ไว้ใน `SKILL.md` หรือ reference เฉพาะเรื่อง ส่วน contract ไว้ใน `schemas/` และการตรวจแบบ deterministic ไว้ใน `scripts/`
- แก้ [`README.md`](README.md) ไปพร้อมกับไฟล์นี้เสมอ
- ห้าม commit credential, secret ของ exchange หรือข้อมูลพอร์ตส่วนตัว
