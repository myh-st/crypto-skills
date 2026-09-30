# TradingView scripts: Spot Co-Trader Ladder

Pine Script v6 versions of the Co-Trader app's daily rule and position ladder
(`crypto_eval/spot_cotrader.py`). Use them on any coin in TradingView. They are decision support
only, not investment advice.

| File | What it is |
|---|---|
| `cotrader_ladder_indicator.pine` | Indicator: trend line, EMA trim level, add-above level, shaded ladder state, B/A/T/S markers, a Thai verdict panel (เริ่มซื้อ / ซื้อเพิ่ม / ลดครึ่ง / ขายออก / ถือต่อ / รอก่อน), alerts and an optional CDC bar colour |
| `cotrader_ladder_strategy.pine` | Strategy for the Strategy Tester: STARTER 25% / FULL 100% of equity by default, 0.1% commission, spot only |

## วิธีติดตั้ง (ภาษาไทย)

1. เปิด TradingView แล้วเปิดกราฟเหรียญที่ต้องการ เช่น `GATEIO:NEARUSDT` ตั้ง timeframe เป็น **1D**
2. กด **Pine Editor** ด้านล่างจอ แล้วเลือก **Open › New blank indicator**
3. ลบโค้ดเดิมทั้งหมด แล้ววางโค้ดจาก `cotrader_ladder_indicator.pine`
4. กด **Save** ตั้งชื่อว่า `Spot Co-Trader Ladder` แล้วกด **Add to chart**
5. สคริปต์นี้จะอยู่ใน **Indicators › My scripts** ถ้าต้องการให้หาง่ายขึ้น กดดาวเพื่อเพิ่มเข้า **Favorites**
6. ถ้าต้องการ backtest ให้ทำแบบเดียวกันด้วยไฟล์ `cotrader_ladder_strategy.pine` เลือก **New blank strategy** แล้วเปิดแท็บ **Strategy Tester**

### ตั้ง Alert ให้เด้งเตือนตอนต้องทำอะไร

- คลิกขวาที่กราฟ แล้วเลือก **Add alert** จากนั้นเลือก Condition = `Spot Co-Trader Ladder`
- เลือกเงื่อนไขอย่างใดอย่างหนึ่ง:
  - **Co-Trader: ladder changed (any)** เตือนทุกครั้งที่ต้องทำอะไร
  - เงื่อนไขเฉพาะ: B เริ่มซื้อ / A ซื้อเพิ่ม / T ลดครึ่ง / S ขายออก
  - **Any alert() function call** ข้อความเตือนจะเป็นภาษาไทย บอกชื่อเหรียญ สิ่งที่ต้องทำ และราคาปิด
- ตั้ง Trigger เป็น **Once Per Bar Close** เพื่อให้เตือนเฉพาะตอนแท่งวันปิดแล้ว ซึ่งตรงกับกฎของเรา
- Alert ต้องตั้งแยกทีละเหรียญ (ทีละกราฟ)

## อ่านแผงและเครื่องหมาย

- **หัวแผง:** บอกว่าต้องทำอะไร เช่น "■ ถือต่อ · เต็มไม้ ไม่ต้องซื้อเพิ่ม" หรือ "▲ ซื้อเพิ่ม · เติมเป็นเต็มไม้"
- **บรรทัดถัดไป:** แสดงเฉพาะราคาที่สำคัญกับสถานะตอนนี้ คือราคาปิดที่จะทำให้ต้อง **ลดครึ่ง** / **ซื้อเพิ่ม** / **ขายหมด** / **เริ่มซื้อ** พร้อมระยะห่างเป็น %
- **เครื่องหมายบนกราฟ** บอกว่าในอดีตกฎสั่งอะไร ณ แท่งไหน:
  - **B** (Buy starter) = เริ่มซื้อครึ่งไม้ เพราะกฎเพิ่งเปลี่ยนเป็นขาขึ้น
  - **A** (Add) = ซื้อเพิ่มจนเต็มไม้ เพราะปิดทำจุดสูงสุดในรอบ 20 วัน
  - **T** (Trim) = ลดเหลือครึ่งไม้ เพราะปิดต่ำกว่า EMA20 แต่เทรนด์ยังขึ้น
  - **S** (Sell all) = ขายหมด ถือเงินสด เพราะหลุดเทรนด์ (ผลตอบแทน 60 วันติดลบ หรือปิดต่ำกว่า SMA100)
- **ปรับขนาดแผง:** ไปที่ Settings › Panel size เลือก Small / Medium / Large

## ต้องรู้

- **ตรงกับแอป:** ค่าเริ่มต้นเหมือนแอปทุกค่า ได้แก่ 60 วัน, SMA100, จุดสูงสุด 20 วัน และ EMA20 ส่วน EMA เริ่มคำนวณจากราคาแรกแบบเดียวกับแอป
- **ความต่างเล็กน้อย:** ถ้าข้อมูลของ exchange ใน TradingView ต่างจาก Gate หรือประวัติราคาสั้นกว่า 1 ปี ค่าอาจต่างจากแอปเล็กน้อย
- **แท่งที่ยังไม่ปิด:** ป้ายคำตัดสินอ่านจาก **แท่งที่ปิดแล้ว** ระหว่างวันแท่งปัจจุบันยังเปลี่ยนได้ และจะยืนยันตอน 07:00 น. (00:00 UTC) ส่วนระยะห่าง % คิดจากราคาปัจจุบัน
- **Strategy:** ส่งคำสั่งตอนแท่งวันปิดแล้วเข้าที่ราคาเปิดของแท่งถัดไป ผลจึงต่างจาก backtest ของแอปเล็กน้อย และผลในอดีตไม่ใช่คำสัญญาถึงอนาคต
- **แถบสี CDC:** ปิดไว้เป็นค่าเริ่มต้น มีไว้เทียบกับ CDC ActionZone ที่คุณใช้อยู่เท่านั้น ระบบ ladder ไม่ได้ใช้ค่านี้
