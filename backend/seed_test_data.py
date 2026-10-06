"""
Seed 20 real-time test records into every major table.
Run from /backend:  .venv-local/Scripts/python.exe seed_test_data.py
"""
import json
import os
import sys
from datetime import datetime, timedelta
from decimal import Decimal
from uuid import uuid4

# ── ensure we import from this package ──────────────────────────────────────
sys.path.insert(0, os.path.dirname(__file__))
os.chdir(os.path.dirname(__file__))

from app.database import SessionLocal, engine, Base
from app.models import (
    Account, AccrualPrepaymentRecord, AppDataRecord, ApprovalMatrixRecord,
    BankAccount, BankStatementLine, BudgetRecord, CashFlowForecastRecord,
    CorporateTaxRecord, CorporateTaxReturn, CostCenterRecord,
    CreditControlRecord, Employee, ExceptionEvent,
    FixedAssetRecord, GeneralLedgerEntry, Invoice, InvoiceLine,
    JournalEntry, JournalLine, MonthEndCloseRecord, Payment, PayrollItem,
    PayrollRun, Receipt, SourceTransaction, SourceTransactionLine,
    StockMovement, StockProductMapping, User, VatReturn, Voucher,
    VoucherLine, VoucherType, Warehouse, Company
)
from app.main import ensure_schema_updates

Base.metadata.create_all(bind=engine)
ensure_schema_updates()

db = SessionLocal()

def uid(): return str(uuid4())
def aed(v): return Decimal(str(v))
def dt(days_ago=0): return datetime.utcnow() - timedelta(days=days_ago)
def ds(days_ago=0): return (datetime.utcnow() - timedelta(days=days_ago)).strftime("%Y-%m-%d")

# ── Resolve company & accounts ───────────────────────────────────────────────
company = db.query(Company).filter(Company.trn == "100000000000003").first()
if not company:
    print("ERROR: main company not found — run the backend first to seed it")
    sys.exit(1)
CID = company.id

def acct(code):
    return db.query(Account).filter(Account.company_id == CID, Account.code == code).first()

ar   = acct("1100")
ap   = acct("2100")
cash = acct("1000")
sales_acc = acct("3000")
purch_acc = acct("4000")
vat_out  = acct("2200")
vat_in   = acct("2210")
sal_exp  = acct("6000")

admin_user = db.query(User).filter(User.email == "admin@taxflowapp.com").first()
ADMIN_ID = admin_user.id if admin_user else None

vt_pay = db.query(VoucherType).filter(VoucherType.company_id == CID, VoucherType.code == "PAY").first()
vt_rct = db.query(VoucherType).filter(VoucherType.company_id == CID, VoucherType.code == "RCT").first()
vt_jrn = db.query(VoucherType).filter(VoucherType.company_id == CID, VoucherType.code == "JRN").first()

print(f"Seeding company: {company.name}  ({CID})")

# ─────────────────────────────────────────────────────────────────────────────
# CUSTOMERS  (AppDataRecord)
# ─────────────────────────────────────────────────────────────────────────────
CUSTOMERS = [
    ("CUST-001","Al Noor Trading LLC","100456789012345","Al Rigga, Deira, Dubai","alnoor@trading.ae","04-2234567"),
    ("CUST-002","Emirates Hospitality Group","100567890123456","Jumeirah Beach Rd, Dubai","info@ehg.ae","04-3456789"),
    ("CUST-003","Dubai Star Electronics","100678901234567","Al Quoz Industrial, Dubai","sales@dubaistar.ae","04-4567890"),
    ("CUST-004","Gulf Medical Supplies FZE","100789012345678","JAFZA, Jebel Ali","orders@gulfmedical.ae","04-8834521"),
    ("CUST-005","Arabian Dreams Events","100890123456789","Sheikh Zayed Rd, Dubai","events@arabiandreams.ae","04-3321456"),
    ("CUST-006","Prime Tech Solutions","100901234567890","Internet City, Dubai","billing@primetech.ae","04-4441234"),
    ("CUST-007","Desert Rose Catering","101012345678901","Al Karama, Dubai","orders@desertrose.ae","04-3332211"),
    ("CUST-008","Horizon Contracting LLC","101123456789012","Sharjah Industrial Area","info@horizoncon.ae","06-5431234"),
    ("CUST-009","Pearl of Arabia Retail","101234567890123","Mall of Emirates, Dubai","retail@pearlarabia.ae","04-3499876"),
    ("CUST-010","Sunrise Fashion House","101345678901234","Gold & Diamond Park, Dubai","orders@sunrisefashion.ae","04-3412345"),
    ("CUST-011","Green Valley Organics","101456789012345","Al Ain Rd, Abu Dhabi","info@greenvalley.ae","02-5512345"),
    ("CUST-012","Falcon Security Services","101567890123456","Business Bay, Dubai","contracts@falconsecurity.ae","04-4523456"),
    ("CUST-013","Blue Ocean Shipping","101678901234567","Port Rashid, Dubai","ops@blueocean.ae","04-3456712"),
    ("CUST-014","Summit Engineering Works","101789012345678","KEZAD, Abu Dhabi","procurement@summit.ae","02-6543210"),
    ("CUST-015","Royal Palm Hotels","101890123456789","Palm Jumeirah, Dubai","finance@royalpalm.ae","04-4456789"),
    ("CUST-016","Al Ameen Pharmacy LLC","101901234567890","Jumeirah, Dubai","purchasing@alameen.ae","04-3449876"),
    ("CUST-017","Nova Digital Agency","102012345678901","DIFC, Dubai","billing@novadigital.ae","04-3678901"),
    ("CUST-018","Oasis Auto Parts","102123456789012","Al Quoz, Dubai","sales@oasisauto.ae","04-3334567"),
    ("CUST-019","Crescent Food Industries","102234567890123","Sharjah Food Zone","orders@crescentfood.ae","06-5123456"),
    ("CUST-020","Titan Logistics FZC","102345678901234","Hamriyah FZ, Sharjah","ops@titanlogistics.ae","06-5234567"),
]

for cid_key, name, trn, addr, email, phone in CUSTOMERS:
    exists = db.query(AppDataRecord).filter(
        AppDataRecord.company_id == CID,
        AppDataRecord.collection == "customers",
        AppDataRecord.record_key == cid_key
    ).first()
    if not exists:
        db.add(AppDataRecord(
            id=uid(), company_id=CID, collection="customers", record_key=cid_key,
            payload=json.dumps({"id": cid_key, "name": name, "trn": trn,
                                "address": addr, "email": email, "phone": phone,
                                "credit_limit": 50000, "outstanding": 0, "status": "active"})
        ))

# ─────────────────────────────────────────────────────────────────────────────
# SUPPLIERS  (AppDataRecord)
# ─────────────────────────────────────────────────────────────────────────────
SUPPLIERS = [
    ("SUPP-001","LG Electronics Gulf FZE","200100000000001","JAFZA, Dubai","supplier@lggulf.ae","04-8812345"),
    ("SUPP-002","Samsung Gulf Electronics","200200000000002","Dubai Silicon Oasis","orders@samsunggulf.ae","04-5001234"),
    ("SUPP-003","Al Futtaim Carillion","200300000000003","Al Garhoud, Dubai","procurement@alfuttaim.ae","04-2943000"),
    ("SUPP-004","Aramex International","200400000000004","DIP, Dubai","billing@aramex.com","04-2853300"),
    ("SUPP-005","National Food Industries","200500000000005","Abu Dhabi Industrial Area","sales@nationalfood.ae","02-5510000"),
    ("SUPP-006","Legrand Gulf FZE","200600000000006","JAFZA South, Dubai","orders@legrandgulf.ae","04-8881234"),
    ("SUPP-007","3M Gulf Limited","200700000000007","JAFZA, Dubai","gulf@mmm.com","04-8822345"),
    ("SUPP-008","Emaar Properties","200800000000008","Downtown Dubai","info@emaar.ae","04-3669666"),
    ("SUPP-009","Al Ghurair Resources","200900000000009","Deira, Dubai","purchasing@alghurair.ae","04-2290000"),
    ("SUPP-010","Bosch Gulf FZE","201000000000010","JAFZA, Dubai","boschgulf@ae.bosch.com","04-8830000"),
    ("SUPP-011","Parker Hannifin FZE","201100000000011","JAFZA, Dubai","middleeast@parker.com","04-8856789"),
    ("SUPP-012","Grundfos Gulf FZE","201200000000012","JAFZA, Dubai","sales@grundfos.ae","04-8090200"),
    ("SUPP-013","Rexnord Middle East","201300000000013","DAFZA, Dubai","rexnord@dafza.ae","04-2992345"),
    ("SUPP-014","Al Naboodah Contracting","201400000000014","Nad Al Hamar, Dubai","info@alnaboodah.ae","04-3347777"),
    ("SUPP-015","Trox Gulf FZE","201500000000015","JAFZA, Dubai","info@troxgulf.ae","04-8838123"),
    ("SUPP-016","Siemens LLC","201600000000016","Barsha South, Dubai","info.ae@siemens.com","04-4959000"),
    ("SUPP-017","ABB LLC","201700000000017","DIFC, Dubai","info@ae.abb.com","04-3647777"),
    ("SUPP-018","Honeywell Middle East","201800000000018","DIFC, Dubai","me@honeywell.com","04-4050000"),
    ("SUPP-019","Schneider Electric","201900000000019","JLT, Dubai","info.ae@se.com","04-3661234"),
    ("SUPP-020","Delta Electronics Gulf","202000000000020","JAFZA, Dubai","sales@deltaelectronics.ae","04-8801234"),
]

for sid, name, trn, addr, email, phone in SUPPLIERS:
    exists = db.query(AppDataRecord).filter(
        AppDataRecord.company_id == CID,
        AppDataRecord.collection == "suppliers",
        AppDataRecord.record_key == sid
    ).first()
    if not exists:
        db.add(AppDataRecord(
            id=uid(), company_id=CID, collection="suppliers", record_key=sid,
            payload=json.dumps({"id": sid, "name": name, "trn": trn,
                                "address": addr, "email": email, "phone": phone,
                                "payment_terms": "Net 30", "status": "active"})
        ))

# ─────────────────────────────────────────────────────────────────────────────
# PRODUCTS  (StockProductMapping + AppDataRecord)
# ─────────────────────────────────────────────────────────────────────────────
PRODUCTS = [
    ("PRD-001","Laptop Dell Inspiron 15","DELL-INS15","PCS",3200,2560,5,0),
    ("PRD-002","iPhone 15 Pro 256GB","APL-IP15P","PCS",4800,4000,5,0),
    ("PRD-003","Samsung 65 QLED TV","SAM-65QLED","PCS",7500,6200,5,0),
    ("PRD-004","Logitech MX Keys Keyboard","LOG-MXKEYS","PCS",380,300,5,0),
    ("PRD-005","HP LaserJet Pro Printer","HP-LJP4001","PCS",1250,1000,5,0),
    ("PRD-006","APC UPS 1500VA","APC-UP1500","PCS",650,520,5,0),
    ("PRD-007","Cisco Catalyst Switch 24P","CSC-CAT24","PCS",2800,2200,5,0),
    ("PRD-008","CCTV Camera Dome 4MP","CCT-DOM4","PCS",280,220,5,0),
    ("PRD-009","LED Panel Light 60x60","LED-PNL60","PCS",95,75,5,0),
    ("PRD-010","Office Chair Ergonomic","FRN-CHR01","PCS",850,670,5,0),
    ("PRD-011","Steel Filing Cabinet 4D","FRN-CAB4D","PCS",720,570,5,0),
    ("PRD-012","Whiteboard 180x120","FRN-WBD18","PCS",420,330,5,0),
    ("PRD-013","A4 Paper Box 5 Ream","STA-A4BOX","BOX",75,58,5,0),
    ("PRD-014","Toner Cartridge HP CF217A","CON-HP217","PCS",145,115,5,0),
    ("PRD-015","Network Cable Cat6 Box","NET-CAT6BX","BOX",320,255,5,0),
    ("PRD-016","Extension Cord 5m 4-Soc","ELC-EXT5M","PCS",85,65,5,0),
    ("PRD-017","Hand Sanitizer 5L","HYG-SAN5L","PCS",55,40,5,0),
    ("PRD-018","Fire Extinguisher 6KG","SAF-EXT6K","PCS",195,155,5,0),
    ("PRD-019","Safety Helmet Hard Hat","SAF-HLM01","PCS",65,48,5,0),
    ("PRD-020","First Aid Kit Large","SAF-FAK01","PCS",180,140,5,0),
]

for sku, name, supplier_name, unit, price, cost, tax_rate, _x in PRODUCTS:
    # StockProductMapping
    exists_spm = db.query(StockProductMapping).filter(
        StockProductMapping.company_id == CID, StockProductMapping.sku == sku
    ).first()
    if not exists_spm:
        markup = round(((price - cost) / cost) * 100, 2)
        vat_amt = round(price * tax_rate / 100, 2)
        db.add(StockProductMapping(
            id=uid(), company_id=CID, sku=sku, name=name,
            supplier_name=supplier_name, taxflow_name=name,
            cost=aed(cost), markup_percent=aed(markup), tax_rate=aed(tax_rate),
            vat_amount=aed(vat_amt), inc_vat=aed(price + vat_amt), price_outer=aed(price),
            tax_code="VAT5", reorder_level=aed(5), units_per_outer=aed(1)
        ))
    # AppDataRecord
    exists_adr = db.query(AppDataRecord).filter(
        AppDataRecord.company_id == CID,
        AppDataRecord.collection == "products",
        AppDataRecord.record_key == sku
    ).first()
    if not exists_adr:
        db.add(AppDataRecord(
            id=uid(), company_id=CID, collection="products", record_key=sku,
            payload=json.dumps({"id": sku, "code": sku, "name": name, "unit": unit,
                                "price": price, "cost": cost, "tax_code": "VAT5",
                                "tax_rate": tax_rate, "status": "active"})
        ))

db.flush()

# ─────────────────────────────────────────────────────────────────────────────
# WAREHOUSES
# ─────────────────────────────────────────────────────────────────────────────
WH_DATA = [
    ("WH-MAIN","Main Warehouse — Al Quoz","Al Quoz Industrial Area, Dubai"),
    ("WH-SHOP","Showroom — Sheikh Zayed","Sheikh Zayed Rd, Dubai"),
    ("WH-JAFZA","JAFZA Bonded Store","JAFZA, Jebel Ali, Dubai"),
]
warehouses = {}
for wcode, wname, wloc in WH_DATA:
    wh = db.query(Warehouse).filter(Warehouse.company_id == CID, Warehouse.name == wname).first()
    if not wh:
        wh = Warehouse(id=uid(), company_id=CID, name=wname, location=wloc)
        db.add(wh)
        db.flush()
    warehouses[wcode] = wh.id

# ─────────────────────────────────────────────────────────────────────────────
# EMPLOYEES
# ─────────────────────────────────────────────────────────────────────────────
EMPS = [
    ("EMP-001","Mohammed Al Rashidi","Operations","Operations Manager",18000,"AE070331234567890001"),
    ("EMP-002","Ahmed Hassan Ibrahim","Finance","Senior Accountant",12500,"AE070331234567890002"),
    ("EMP-003","Fatima Al Zaabi","Sales","Sales Manager",15000,"AE070331234567890003"),
    ("EMP-004","Khalid Bin Hamdan","Procurement","Procurement Officer",9500,"AE070331234567890004"),
    ("EMP-005","Sara Al Mansoori","HR","HR Coordinator",8500,"AE070331234567890005"),
    ("EMP-006","Ali Mohammed Al Ali","IT","IT Administrator",11000,"AE070331234567890006"),
    ("EMP-007","Nour Al Khatib","Finance","Junior Accountant",7500,"AE070331234567890007"),
    ("EMP-008","Rashid Al Falasi","Logistics","Logistics Supervisor",10500,"AE070331234567890008"),
    ("EMP-009","Maryam Saeed Al Dhaheri","Admin","Executive Assistant",7000,"AE070331234567890009"),
    ("EMP-010","Tariq Al Marzouqi","Sales","Sales Executive",8000,"AE070331234567890010"),
    ("EMP-011","Priya Sharma","Finance","Accounts Payable",6500,"AE070331234567890011"),
    ("EMP-012","Ravi Kumar Singh","IT","Software Developer",14000,"AE070331234567890012"),
    ("EMP-013","James O'Brien","Sales","Business Development",13500,"AE070331234567890013"),
    ("EMP-014","Maria Santos","Admin","Receptionist",5500,"AE070331234567890014"),
    ("EMP-015","Ahmed Al Suwaidi","Operations","Warehouse Supervisor",9000,"AE070331234567890015"),
    ("EMP-016","Layla Khalifa Al Neyadi","Marketing","Digital Marketing Exec",8800,"AE070331234567890016"),
    ("EMP-017","Sanjay Patel","Logistics","Driver — Senior",5000,"AE070331234567890017"),
    ("EMP-018","Fatima Bint Zayed","Compliance","Compliance Officer",12000,"AE070331234567890018"),
    ("EMP-019","Hassan Al Shammari","Operations","Technician",6800,"AE070331234567890019"),
    ("EMP-020","Aisha Mohammed Al Qassim","Finance","Finance Manager",20000,"AE070331234567890020"),
]

emp_ids = {}
for eno, name, dept, desig, salary, iban in EMPS:
    emp = db.query(Employee).filter(Employee.company_id == CID, Employee.employee_no == eno).first()
    if not emp:
        emp = Employee(id=uid(), company_id=CID, employee_no=eno, full_name=name,
                       department=dept, designation=desig, basic_salary=aed(salary),
                       iban=iban, status="active")
        db.add(emp)
        db.flush()
    emp_ids[eno] = emp.id
    # also keep in AppDataRecord for frontend sync
    exists_adr = db.query(AppDataRecord).filter(
        AppDataRecord.company_id == CID,
        AppDataRecord.collection == "employees",
        AppDataRecord.record_key == eno
    ).first()
    if not exists_adr:
        db.add(AppDataRecord(
            id=uid(), company_id=CID, collection="employees", record_key=eno,
            payload=json.dumps({"id": eno, "employee_no": eno, "name": name,
                                "department": dept, "designation": desig,
                                "salary": salary, "iban": iban, "status": "active"})
        ))

db.flush()

# ─────────────────────────────────────────────────────────────────────────────
# SALES INVOICES  (Invoice + InvoiceLine + AppDataRecord)
# ─────────────────────────────────────────────────────────────────────────────
SINV = [
    ("INV-2026-001","CUST-001","Al Noor Trading LLC",ds(45),"Laptop Dell Inspiron 15",5,3200,0,"paid"),
    ("INV-2026-002","CUST-002","Emirates Hospitality Group",ds(40),"LED Panel Light 60x60",50,95,0,"paid"),
    ("INV-2026-003","CUST-003","Dubai Star Electronics",ds(35),"iPhone 15 Pro 256GB",10,4800,0,"issued"),
    ("INV-2026-004","CUST-004","Gulf Medical Supplies FZE",ds(32),"Samsung 65 QLED TV",3,7500,0,"issued"),
    ("INV-2026-005","CUST-005","Arabian Dreams Events",ds(30),"Office Chair Ergonomic",20,850,0,"issued"),
    ("INV-2026-006","CUST-006","Prime Tech Solutions",ds(28),"Cisco Catalyst Switch 24P",2,2800,0,"paid"),
    ("INV-2026-007","CUST-007","Desert Rose Catering",ds(25),"Hand Sanitizer 5L",100,55,0,"paid"),
    ("INV-2026-008","CUST-008","Horizon Contracting LLC",ds(22),"Safety Helmet Hard Hat",50,65,0,"issued"),
    ("INV-2026-009","CUST-009","Pearl of Arabia Retail",ds(20),"Steel Filing Cabinet 4D",10,720,0,"issued"),
    ("INV-2026-010","CUST-010","Sunrise Fashion House",ds(18),"Whiteboard 180x120",5,420,0,"draft"),
    ("INV-2026-011","CUST-011","Green Valley Organics",ds(16),"First Aid Kit Large",20,180,0,"issued"),
    ("INV-2026-012","CUST-012","Falcon Security Services",ds(14),"CCTV Camera Dome 4MP",15,280,0,"paid"),
    ("INV-2026-013","CUST-013","Blue Ocean Shipping",ds(12),"APC UPS 1500VA",4,650,0,"issued"),
    ("INV-2026-014","CUST-014","Summit Engineering Works",ds(10),"HP LaserJet Pro Printer",5,1250,0,"issued"),
    ("INV-2026-015","CUST-015","Royal Palm Hotels",ds(9),"Network Cable Cat6 Box",10,320,0,"paid"),
    ("INV-2026-016","CUST-016","Al Ameen Pharmacy LLC",ds(8),"A4 Paper Box 5 Ream",30,75,0,"issued"),
    ("INV-2026-017","CUST-017","Nova Digital Agency",ds(7),"Logitech MX Keys Keyboard",8,380,0,"draft"),
    ("INV-2026-018","CUST-018","Oasis Auto Parts",ds(5),"Fire Extinguisher 6KG",10,195,0,"issued"),
    ("INV-2026-019","CUST-019","Crescent Food Industries",ds(3),"Toner Cartridge HP CF217A",20,145,0,"draft"),
    ("INV-2026-020","CUST-020","Titan Logistics FZC",ds(1),"Extension Cord 5m 4-Soc",25,85,0,"draft"),
]

for inv_no, cust_id, cust_name, inv_date, prod_desc, qty, unit_price, _, status in SINV:
    exists = db.query(Invoice).filter(
        Invoice.company_id == CID, Invoice.invoice_number == inv_no
    ).first()
    if not exists:
        sub = aed(qty * unit_price)
        vat = aed(round(float(sub) * 0.05, 2))
        tot = sub + vat
        inv = Invoice(id=uid(), company_id=CID, customer_name=cust_name,
                      invoice_number=inv_no, status=status,
                      subtotal=sub, vat=vat, total=tot)
        db.add(inv)
        db.flush()
        db.add(InvoiceLine(id=uid(), invoice_id=inv.id, description=prod_desc,
                           quantity=aed(qty), unit_price=aed(unit_price), vat_rate=aed(5)))
        # AppDataRecord for frontend table
        due = (datetime.strptime(inv_date, "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d")
        db.add(AppDataRecord(
            id=uid(), company_id=CID, collection="salesInvoices", record_key=inv_no,
            payload=json.dumps({
                "id": inv_no, "invoice_no": inv_no, "customer": cust_name,
                "customer_id": cust_id, "date": inv_date, "due_date": due,
                "lines": [{"product": prod_desc, "unit": "PCS", "qty": qty, "price": unit_price,
                           "amount": qty * unit_price}],
                "subtotal": float(sub), "vat": float(vat), "total": float(tot), "status": status,
                "source": "manual"
            })
        ))

db.flush()

# ─────────────────────────────────────────────────────────────────────────────
# PURCHASE INVOICES  (SourceTransaction + AppDataRecord)
# ─────────────────────────────────────────────────────────────────────────────
PINV = [
    ("PINV-2026-001","SUPP-001","LG Electronics Gulf FZE",ds(44),"LED Panel Light 60x60",100,75,5,"approved"),
    ("PINV-2026-002","SUPP-002","Samsung Gulf Electronics",ds(39),"Samsung 65 QLED TV",2,6200,5,"approved"),
    ("PINV-2026-003","SUPP-003","Al Futtaim Carillion",ds(34),"Office Furniture Lot",1,15000,5,"approved"),
    ("PINV-2026-004","SUPP-004","Aramex International",ds(31),"Courier Services Mar",1,4500,5,"approved"),
    ("PINV-2026-005","SUPP-005","National Food Industries",ds(29),"Stationery Supplies",50,58,5,"approved"),
    ("PINV-2026-006","SUPP-006","Legrand Gulf FZE",ds(27),"Network Cable Cat6 Box",20,255,5,"approved"),
    ("PINV-2026-007","SUPP-007","3M Gulf Limited",ds(24),"Safety Equipment Lot",1,8200,5,"approved"),
    ("PINV-2026-008","SUPP-008","Emaar Properties",ds(21),"Office Rent Q2",1,42000,0,"approved"),
    ("PINV-2026-009","SUPP-009","Al Ghurair Resources",ds(19),"Raw Material Batch",1,28000,5,"pending"),
    ("PINV-2026-010","SUPP-010","Bosch Gulf FZE",ds(17),"CCTV Camera Dome 4MP",8,220,5,"approved"),
    ("PINV-2026-011","SUPP-011","Parker Hannifin FZE",ds(15),"Spare Parts Q2",1,6400,5,"approved"),
    ("PINV-2026-012","SUPP-012","Grundfos Gulf FZE",ds(13),"Pump Maintenance",1,3800,5,"approved"),
    ("PINV-2026-013","SUPP-013","Rexnord Middle East",ds(11),"Mechanical Parts",1,5200,5,"pending"),
    ("PINV-2026-014","SUPP-014","Al Naboodah Contracting",ds(10),"Civil Works Invoice",1,35000,5,"approved"),
    ("PINV-2026-015","SUPP-015","Trox Gulf FZE",ds(8),"HVAC Components",1,12500,5,"approved"),
    ("PINV-2026-016","SUPP-016","Siemens LLC",ds(7),"Electrical Panels",3,9800,5,"approved"),
    ("PINV-2026-017","SUPP-017","ABB LLC",ds(6),"Circuit Breakers Lot",1,4200,5,"pending"),
    ("PINV-2026-018","SUPP-018","Honeywell Middle East",ds(4),"Control Systems",1,18500,5,"approved"),
    ("PINV-2026-019","SUPP-019","Schneider Electric",ds(2),"Distribution Board",2,7200,5,"draft"),
    ("PINV-2026-020","SUPP-020","Delta Electronics Gulf",ds(1),"UPS Systems",3,4200,5,"draft"),
]

for ref, supp_id, supp_name, p_date, desc, qty, unit_price, vat_pct, status in PINV:
    exists = db.query(SourceTransaction).filter(
        SourceTransaction.company_id == CID, SourceTransaction.reference == ref
    ).first()
    if not exists:
        sub = aed(qty * unit_price)
        vat = aed(round(float(sub) * vat_pct / 100, 2))
        tot = sub + vat
        st = SourceTransaction(id=uid(), company_id=CID, module="purchase",
                               reference=ref, party_name=supp_name, status=status,
                               subtotal=sub, vat=vat, total=tot)
        db.add(st)
        db.flush()
        db.add(SourceTransactionLine(
            id=uid(), source_id=st.id, description=desc, account_code="4000",
            quantity=aed(qty), unit_price=aed(unit_price), vat_rate=aed(vat_pct),
            amount=sub, vat_amount=vat
        ))
        due = (datetime.strptime(p_date, "%Y-%m-%d") + timedelta(days=30)).strftime("%Y-%m-%d")
        db.add(AppDataRecord(
            id=uid(), company_id=CID, collection="purchaseInvoices", record_key=ref,
            payload=json.dumps({
                "id": ref, "invoice_no": ref, "supplier": supp_name, "supplier_id": supp_id,
                "date": p_date, "due_date": due,
                "lines": [{"product": desc, "qty": qty, "price": unit_price,
                           "amount": qty * unit_price}],
                "subtotal": float(sub), "vat": float(vat), "total": float(tot),
                "status": status, "source": "manual"
            })
        ))

db.flush()

# ─────────────────────────────────────────────────────────────────────────────
# VOUCHERS — Payment (PAY) + Receipt (RCT) + Journal (JRN)
# ─────────────────────────────────────────────────────────────────────────────
if vt_pay and vt_rct and vt_jrn and ar and ap and cash:
    voucher_data = [
        # (voucher_no, type_id, party, amount, narration, status)
        ("PAY-2026-001", vt_pay.id, "LG Electronics Gulf FZE", 16537.50, "Payment for PINV-2026-001", "approved"),
        ("PAY-2026-002", vt_pay.id, "Aramex International", 4725.00, "Payment for PINV-2026-004", "approved"),
        ("PAY-2026-003", vt_pay.id, "Emaar Properties", 42000.00, "Office rent Q2", "approved"),
        ("PAY-2026-004", vt_pay.id, "National Food Industries", 3045.00, "Stationery payment", "approved"),
        ("PAY-2026-005", vt_pay.id, "Siemens LLC", 30870.00, "Electrical panels payment", "draft"),
        ("PAY-2026-006", vt_pay.id, "Al Naboodah Contracting", 36750.00, "Civil works progress payment", "approved"),
        ("PAY-2026-007", vt_pay.id, "Honeywell Middle East", 19425.00, "Control systems payment", "approved"),
        ("PAY-2026-008", vt_pay.id, "Bosch Gulf FZE", 1848.00, "CCTV cameras payment", "approved"),
        ("PAY-2026-009", vt_pay.id, "Trox Gulf FZE", 13125.00, "HVAC components payment", "draft"),
        ("PAY-2026-010", vt_pay.id, "Al Ghurair Resources", 14700.00, "Partial raw material payment", "approved"),
        ("RCT-2026-001", vt_rct.id, "Al Noor Trading LLC", 16800.00, "Receipt for INV-2026-001", "approved"),
        ("RCT-2026-002", vt_rct.id, "Emirates Hospitality Group", 4987.50, "Receipt for INV-2026-002", "approved"),
        ("RCT-2026-003", vt_rct.id, "Prime Tech Solutions", 5880.00, "Receipt for INV-2026-006", "approved"),
        ("RCT-2026-004", vt_rct.id, "Desert Rose Catering", 5775.00, "Receipt for INV-2026-007", "approved"),
        ("RCT-2026-005", vt_rct.id, "Falcon Security Services", 4410.00, "Receipt for INV-2026-012", "approved"),
        ("RCT-2026-006", vt_rct.id, "Blue Ocean Shipping", 2730.00, "Receipt for INV-2026-013", "approved"),
        ("RCT-2026-007", vt_rct.id, "Royal Palm Hotels", 3360.00, "Receipt for INV-2026-015", "approved"),
        ("RCT-2026-008", vt_rct.id, "Al Noor Trading LLC", 8000.00, "Advance payment from Al Noor", "approved"),
        ("RCT-2026-009", vt_rct.id, "Gulf Medical Supplies FZE", 23625.00, "Receipt for INV-2026-004", "approved"),
        ("RCT-2026-010", vt_rct.id, "Summit Engineering Works", 6562.50, "Receipt for INV-2026-014", "approved"),
    ]
    for vno, vtype_id, party, amount, narr, vstatus in voucher_data:
        exists = db.query(Voucher).filter(
            Voucher.company_id == CID, Voucher.voucher_no == vno
        ).first()
        if not exists:
            v = Voucher(id=uid(), company_id=CID, voucher_type_id=vtype_id,
                        voucher_no=vno, party=party, narration=narr, status=vstatus)
            db.add(v)
            db.flush()
            is_pay = vno.startswith("PAY")
            db.add(VoucherLine(id=uid(), voucher_id=v.id,
                               account_id=ap.id if is_pay else ar.id,
                               debit=aed(amount) if is_pay else aed(0),
                               credit=aed(0) if is_pay else aed(amount),
                               party=party, narration=narr))
            db.add(VoucherLine(id=uid(), voucher_id=v.id,
                               account_id=cash.id,
                               debit=aed(0) if is_pay else aed(amount),
                               credit=aed(amount) if is_pay else aed(0),
                               narration=narr))

db.flush()

# ─────────────────────────────────────────────────────────────────────────────
# PAYMENTS model
# ─────────────────────────────────────────────────────────────────────────────
if cash and ap:
    PAY_DATA = [
        ("PMT-001","Siemens LLC",30870.00,ds(7),"TT","CHQ-001"),
        ("PMT-002","ABB LLC",4410.00,ds(6),"bank","TT-002"),
        ("PMT-003","Schneider Electric",7560.00,ds(5),"bank","TT-003"),
        ("PMT-004","Delta Electronics Gulf",4410.00,ds(4),"bank","TT-004"),
        ("PMT-005","Legrand Gulf FZE",5355.00,ds(3),"bank","TT-005"),
    ]
    for pno, payee, amt, pdate, mode, ref in PAY_DATA:
        exists = db.query(Payment).filter(
            Payment.company_id == CID, Payment.payment_no == pno
        ).first()
        if not exists:
            db.add(Payment(
                id=uid(), company_id=CID, payment_no=pno,
                payment_date=datetime.strptime(pdate, "%Y-%m-%d"),
                payment_mode=mode, cash_bank_account_id=cash.id,
                debit_account_id=ap.id, payee_type="supplier",
                payee_name=payee, amount=aed(amt), reference_no=ref,
                narration=f"Payment to {payee}", status="approved"
            ))

# ─────────────────────────────────────────────────────────────────────────────
# RECEIPTS model
# ─────────────────────────────────────────────────────────────────────────────
if cash and ar:
    RCT_DATA = [
        ("RCT-PMT-001","Crescent Food Industries",21000.00,ds(7),"bank","CHQ-R001"),
        ("RCT-PMT-002","Titan Logistics FZC",2231.25,ds(5),"bank","TT-R002"),
        ("RCT-PMT-003","Nova Digital Agency",3192.00,ds(4),"bank","TT-R003"),
        ("RCT-PMT-004","Oasis Auto Parts",2047.50,ds(2),"bank","TT-R004"),
        ("RCT-PMT-005","Al Ameen Pharmacy LLC",2362.50,ds(1),"cash","CASH-R005"),
    ]
    for rno, from_name, amt, rdate, mode, ref in RCT_DATA:
        exists = db.query(Receipt).filter(
            Receipt.company_id == CID, Receipt.receipt_no == rno
        ).first()
        if not exists:
            db.add(Receipt(
                id=uid(), company_id=CID, receipt_no=rno,
                receipt_date=datetime.strptime(rdate, "%Y-%m-%d"),
                receipt_mode=mode, cash_bank_account_id=cash.id,
                credit_account_id=ar.id, received_from=from_name,
                amount=aed(amt), reference_no=ref,
                narration=f"Receipt from {from_name}", status="approved"
            ))

db.flush()

# ─────────────────────────────────────────────────────────────────────────────
# JOURNAL ENTRIES  (posted)
# ─────────────────────────────────────────────────────────────────────────────
if ar and sales_acc and vat_out and ap and purch_acc and vat_in and cash and sal_exp:
    JE_DATA = [
        ("JRN-2026-001", "Sales Invoice INV-2026-001 posting", ds(45), ar, sales_acc, vat_out, 16000, 800),
        ("JRN-2026-002", "Sales Invoice INV-2026-002 posting", ds(40), ar, sales_acc, vat_out, 4750, 237.50),
        ("JRN-2026-003", "Sales Invoice INV-2026-003 posting", ds(35), ar, sales_acc, vat_out, 48000, 2400),
        ("JRN-2026-004", "Purchase Invoice PINV-2026-001 posting", ds(44), purch_acc, vat_in, ap, 7500, 375),
        ("JRN-2026-005", "Purchase Invoice PINV-2026-003 posting", ds(34), purch_acc, vat_in, ap, 15000, 750),
        ("JRN-2026-006", "Office Rent Mar 2026", ds(30), purch_acc, None, ap, 42000, 0),
        ("JRN-2026-007", "Salary expense Mar 2026", ds(28), sal_exp, None, cash, 85000, 0),
        ("JRN-2026-008", "Receipt from Al Noor Trading", ds(25), cash, None, ar, 16800, 0),
        ("JRN-2026-009", "Receipt from Emirates Hospitality", ds(22), cash, None, ar, 4987.50, 0),
        ("JRN-2026-010", "Payment to Emaar Properties", ds(20), ap, None, cash, 42000, 0),
    ]
    for je_no, desc, je_date, debit_acc, debit_acc2, credit_acc, base_amt, vat_amt_val in JE_DATA:
        exists = db.query(JournalEntry).filter(
            JournalEntry.company_id == CID, JournalEntry.entry_number == je_no
        ).first()
        if not exists:
            je = JournalEntry(id=uid(), company_id=CID, entry_number=je_no,
                              description=desc, source_module="manual",
                              entry_date=datetime.strptime(je_date, "%Y-%m-%d"),
                              status="posted")
            db.add(je)
            db.flush()
            total_dr = aed(base_amt + vat_amt_val)
            if debit_acc2 and debit_acc is ar:
                # Sales: Dr Receivable (gross) / Cr Sales (net) / Cr VAT Output (VAT)
                db.add(JournalLine(id=uid(), journal_id=je.id, account_id=ar.id,
                                   debit=total_dr, credit=aed(0), description=desc))
                db.add(JournalLine(id=uid(), journal_id=je.id, account_id=debit_acc2.id,
                                   debit=aed(0), credit=aed(base_amt)))
                db.add(JournalLine(id=uid(), journal_id=je.id, account_id=credit_acc.id,
                                   debit=aed(0), credit=aed(vat_amt_val)))
            elif debit_acc2:
                # Purchases: Dr Purchases (net) / Dr VAT Input (VAT) / Cr Payable (gross)
                db.add(JournalLine(id=uid(), journal_id=je.id, account_id=debit_acc.id,
                                   debit=aed(base_amt), credit=aed(0), description=desc))
                db.add(JournalLine(id=uid(), journal_id=je.id, account_id=debit_acc2.id,
                                   debit=aed(vat_amt_val), credit=aed(0)))
                db.add(JournalLine(id=uid(), journal_id=je.id, account_id=credit_acc.id,
                                   debit=aed(0), credit=total_dr))
            else:
                db.add(JournalLine(id=uid(), journal_id=je.id, account_id=debit_acc.id,
                                   debit=total_dr, credit=aed(0), description=desc))
                db.add(JournalLine(id=uid(), journal_id=je.id, account_id=credit_acc.id,
                                   debit=aed(0), credit=total_dr, description=desc))

db.flush()

# ─────────────────────────────────────────────────────────────────────────────
# PAYROLL  (1 run with 20 items)
# ─────────────────────────────────────────────────────────────────────────────
period = "2026-05"
run = db.query(PayrollRun).filter(PayrollRun.company_id == CID, PayrollRun.period == period).first()
if not run:
    gross = sum(s for _, _, _, _, s, _ in EMPS)
    housing = round(gross * 0.20, 2)
    transport = round(gross * 0.10, 2)
    deductions = round(gross * 0.02, 2)
    net = gross + housing + transport - deductions
    run = PayrollRun(id=uid(), company_id=CID, period=period, status="approved",
                     gross_total=aed(gross + housing + transport),
                     deductions_total=aed(deductions),
                     net_total=aed(net))
    db.add(run)
    db.flush()
    for eno, name, dept, desig, salary, iban in EMPS:
        eid = emp_ids.get(eno)
        if eid:
            housing_val = round(salary * 0.20, 2)
            transport_val = round(salary * 0.10, 2)
            deduct_val = round(salary * 0.02, 2)
            net_pay = salary + housing_val + transport_val - deduct_val
            db.add(PayrollItem(
                id=uid(), run_id=run.id, employee_id=eid,
                basic=aed(salary), allowances=aed(housing_val + transport_val),
                overtime=aed(0), deductions=aed(deduct_val), net_pay=aed(net_pay),
                wps_status="ready"
            ))

db.flush()

# ─────────────────────────────────────────────────────────────────────────────
# BANK ACCOUNT + BANK STATEMENT LINES
# ─────────────────────────────────────────────────────────────────────────────
ba = db.query(BankAccount).filter(BankAccount.company_id == CID).first()
if not ba and cash:
    ba = BankAccount(id=uid(), company_id=CID, account_id=cash.id,
                     bank_name="Emirates NBD", iban="AE070331234567890000001",
                     account_number="1015277554001", currency="AED", status="active")
    db.add(ba)
    db.flush()

if ba:
    BSL_DATA = [
        (ds(30),"TT IN","INV-2026-001-RCT","Al Noor Trading LLC",0,16800.00),
        (ds(28),"TT IN","INV-2026-002-RCT","Emirates Hospitality Group",0,4987.50),
        (ds(26),"TT OUT","PINV-2026-001-PAY","LG Electronics Gulf FZE",16537.50,0),
        (ds(25),"TT OUT","PINV-2026-004-PAY","Aramex International",4725.00,0),
        (ds(24),"TT IN","INV-2026-006-RCT","Prime Tech Solutions",0,5880.00),
        (ds(22),"TT OUT","PINV-2026-008-PAY","Emaar Properties",42000.00,0),
        (ds(21),"TT IN","INV-2026-007-RCT","Desert Rose Catering",0,5775.00),
        (ds(20),"SAL-MAY","SALARY-MAY","Payroll May 2026",85000.00,0),
        (ds(18),"TT IN","INV-2026-012-RCT","Falcon Security Services",0,4410.00),
        (ds(16),"TT OUT","PINV-2026-007-PAY","3M Gulf Limited",8610.00,0),
        (ds(14),"TT IN","INV-2026-013-RCT","Blue Ocean Shipping",0,2730.00),
        (ds(12),"TT OUT","PINV-2026-014-PAY","Al Naboodah Contracting",36750.00,0),
        (ds(10),"TT IN","INV-2026-015-RCT","Royal Palm Hotels",0,3360.00),
        (ds(9),"TT OUT","PINV-2026-015-PAY","Trox Gulf FZE",13125.00,0),
        (ds(8),"TT IN","INV-2026-004-RCT","Gulf Medical Supplies FZE",0,23625.00),
        (ds(7),"TT OUT","PINV-2026-016-PAY","Siemens LLC",30870.00,0),
        (ds(6),"TT OUT","PINV-2026-018-PAY","Honeywell Middle East",19425.00,0),
        (ds(5),"TT IN","INV-2026-014-RCT","Summit Engineering Works",0,6562.50),
        (ds(3),"TT IN","ADVANCE-001","Al Noor Trading LLC",0,8000.00),
        (ds(1),"TT OUT","PINV-2026-009-PAY","Al Ghurair Resources",14700.00,0),
    ]
    for txn_date, txn_type, ref_no, party, debit, credit in BSL_DATA:
        exists = db.query(BankStatementLine).filter(
            BankStatementLine.bank_account_id == ba.id,
            BankStatementLine.reference_no == ref_no
        ).first()
        if not exists:
            db.add(BankStatementLine(
                id=uid(), company_id=CID, bank_account_id=ba.id,
                statement_date=txn_date, transaction_date=txn_date,
                reference_no=ref_no, party_name=party,
                narration=f"{txn_type} — {party}",
                debit=aed(debit), credit=aed(credit), status="unmatched"
            ))

db.flush()

# ─────────────────────────────────────────────────────────────────────────────
# STOCK MOVEMENTS
# ─────────────────────────────────────────────────────────────────────────────
wh_main = warehouses.get("WH-MAIN")
for sku, name, _, unit, price, cost, tax_rate, _ in PRODUCTS[:10]:
    spm = db.query(StockProductMapping).filter(
        StockProductMapping.company_id == CID, StockProductMapping.sku == sku
    ).first()
    if spm and wh_main:
        exists = db.query(StockMovement).filter(
            StockMovement.mapping_id == spm.id, StockMovement.reference == f"GRN-{sku}"
        ).first()
        if not exists:
            db.add(StockMovement(
                id=uid(), company_id=CID, mapping_id=spm.id, warehouse_id=wh_main,
                movement_type="in", quantity=aed(20), unit_cost=aed(cost),
                reference=f"GRN-{sku}"
            ))

db.flush()

# ─────────────────────────────────────────────────────────────────────────────
# VAT RETURNS
# ─────────────────────────────────────────────────────────────────────────────
VAT_RETURNS = [
    ("2025-Q3","2025-07","2025-09",185000,9250,142000,7100,"filed","FTA-2025Q3-001234"),
    ("2025-Q4","2025-10","2025-12",210000,10500,168000,8400,"filed","FTA-2025Q4-005678"),
    ("2026-Q1","2026-01","2026-03",248000,12400,195000,9750,"submitted","FTA-2026Q1-009012"),
    ("2026-Q2","2026-04","2026-06",0,0,0,0,"draft",None),
]
for period_name, start_m, end_m, sales_ta, out_vat, pur_ta, in_vat, filing, fta_ref in VAT_RETURNS:
    exists = db.query(VatReturn).filter(
        VatReturn.company_id == CID, VatReturn.period == period_name
    ).first()
    if not exists:
        net = aed(out_vat - in_vat)
        db.add(VatReturn(
            id=uid(), company_id=CID, period=period_name,
            sales_taxable_amount=aed(sales_ta), output_vat=aed(out_vat),
            purchase_taxable_amount=aed(pur_ta), input_vat=aed(in_vat),
            net_vat=net, filing_status=filing, fta_reference_no=fta_ref
        ))

# ─────────────────────────────────────────────────────────────────────────────
# CORPORATE TAX RETURNS
# ─────────────────────────────────────────────────────────────────────────────
CT_RETURNS = [
    ("FY-2024","2024-01","2024-12",850000,45000,0,0,"filed","CT-2024-00567"),
    ("FY-2025","2025-01","2025-12",1120000,58000,0,0,"submitted","CT-2025-01023"),
]
for tp, _s, _e, profit, non_ded, exempt, loss_adj, filing, ref in CT_RETURNS:
    exists = db.query(CorporateTaxReturn).filter(
        CorporateTaxReturn.company_id == CID, CorporateTaxReturn.tax_period == tp
    ).first()
    if not exists:
        taxable = profit + non_ded - exempt - loss_adj
        tax_payable = max(0, round((taxable - 375000) * 0.09, 2)) if taxable > 375000 else 0
        db.add(CorporateTaxReturn(
            id=uid(), company_id=CID, tax_period=tp,
            accounting_profit=aed(profit), non_deductible_expenses=aed(non_ded),
            exempt_income=aed(exempt), tax_loss_adjustment=aed(loss_adj),
            taxable_income=aed(taxable), tax_rate=aed(9),
            corporate_tax_payable=aed(tax_payable), filing_status=filing,
            reference_no=ref
        ))

# ─────────────────────────────────────────────────────────────────────────────
# FIXED ASSETS
# ─────────────────────────────────────────────────────────────────────────────
ASSETS = [
    ("FA-001","Server Dell PowerEdge R750","IT Equipment",28500,5700,"active","Server Room","IT Dept"),
    ("FA-002","Forklift Crown FC5200","Material Handling",45000,9000,"active","Main Warehouse","Operations"),
    ("FA-003","Pickup Toyota Hilux 2023","Motor Vehicle",98000,19600,"active","Vehicle Pool","Logistics"),
    ("FA-004","Air Conditioner Daikin 5T","HVAC Equipment",12500,2500,"active","HQ Office","Facilities"),
    ("FA-005","CCTV System 32-Channel","Security Equipment",18000,3600,"active","HQ Office","Security"),
    ("FA-006","Solar Panels 50kW System","Renewable Energy",125000,25000,"active","Rooftop","Facilities"),
    ("FA-007","Backup Generator 100kVA","Power Equipment",85000,17000,"active","Compound","Facilities"),
    ("FA-008","Office Furniture Set A","Furniture & Fixtures",35000,7000,"active","HQ Office","Admin"),
    ("FA-009","Weighbridge 50T Digital","Weighing Equipment",55000,11000,"active","Main Warehouse","Operations"),
    ("FA-010","ERP Server (Backup)","IT Equipment",22000,4400,"active","Server Room","IT Dept"),
]
for code, name, cat, cost, acc_dep, status, loc, cust in ASSETS:
    exists = db.query(FixedAssetRecord).filter(
        FixedAssetRecord.company_id == CID, FixedAssetRecord.asset_code == code
    ).first()
    if not exists:
        db.add(FixedAssetRecord(
            id=uid(), company_id=CID, asset_code=code, asset_name=name,
            category=cat, purchase_cost=aed(cost), accumulated_depreciation=aed(acc_dep),
            method="Straight Line", location=loc, custodian=cust, status=status
        ))

# ─────────────────────────────────────────────────────────────────────────────
# COST CENTERS
# ─────────────────────────────────────────────────────────────────────────────
CC_DATA = [
    ("CC-OPS","Operations","Operations","HQ","General Ops","Dubai"),
    ("CC-SALES","Sales & Marketing","Sales","HQ","Revenue","Dubai"),
    ("CC-FINANCE","Finance & Accounting","Finance","HQ","Support","Dubai"),
    ("CC-IT","Information Technology","IT","HQ","Support","Dubai"),
    ("CC-HR","Human Resources","HR","HQ","Support","Dubai"),
    ("CC-PROJ-A","Project Alpha","Operations","Field","Project","Abu Dhabi"),
    ("CC-PROJ-B","Project Beta","Operations","Field","Project","Sharjah"),
    ("CC-WH","Warehouse Operations","Operations","HQ","Logistics","Dubai"),
]
for code, name, dept, branch, proj, loc in CC_DATA:
    exists = db.query(CostCenterRecord).filter(
        CostCenterRecord.company_id == CID, CostCenterRecord.code == code
    ).first()
    if not exists:
        db.add(CostCenterRecord(
            id=uid(), company_id=CID, code=code, name=name,
            department=dept, branch=branch, project=proj, location=loc, status="active"
        ))

# ─────────────────────────────────────────────────────────────────────────────
# BUDGET RECORDS
# ─────────────────────────────────────────────────────────────────────────────
BUDGET_DATA = [
    ("FY-2026","CC-SALES","3000",1500000,680000),
    ("FY-2026","CC-OPS","4000",900000,412000),
    ("FY-2026","CC-OPS","5000",450000,198000),
    ("FY-2026","CC-HR","6000",1200000,522000),
    ("FY-2026","CC-FINANCE","5100",150000,64000),
    ("FY-2026","CC-IT","5000",180000,72000),
    ("FY-2026","CC-WH","4000",320000,128000),
    ("FY-2026","CC-PROJ-A","4000",750000,310000),
]
for fy, cc, acc_code, budget, actual in BUDGET_DATA:
    exists = db.query(BudgetRecord).filter(
        BudgetRecord.company_id == CID, BudgetRecord.fiscal_year == fy,
        BudgetRecord.cost_center == cc, BudgetRecord.account_code == acc_code
    ).first()
    if not exists:
        variance = budget - actual
        db.add(BudgetRecord(
            id=uid(), company_id=CID, fiscal_year=fy, cost_center=cc,
            account_code=acc_code, annual_budget=aed(budget),
            actual_amount=aed(actual), variance_amount=aed(variance),
            approval_status="approved"
        ))

# ─────────────────────────────────────────────────────────────────────────────
# CASH FLOW FORECAST
# ─────────────────────────────────────────────────────────────────────────────
CF_DATA = [
    (ds(0), 85000, 62000),
    ((datetime.utcnow() + timedelta(days=7)).strftime("%Y-%m-%d"), 42000, 55000),
    ((datetime.utcnow() + timedelta(days=14)).strftime("%Y-%m-%d"), 95000, 38000),
    ((datetime.utcnow() + timedelta(days=21)).strftime("%Y-%m-%d"), 62000, 48000),
    ((datetime.utcnow() + timedelta(days=30)).strftime("%Y-%m-%d"), 120000, 75000),
]
for fdate, receipts, payments in CF_DATA:
    exists = db.query(CashFlowForecastRecord).filter(
        CashFlowForecastRecord.company_id == CID,
        CashFlowForecastRecord.forecast_date == fdate
    ).first()
    if not exists:
        db.add(CashFlowForecastRecord(
            id=uid(), company_id=CID, forecast_date=fdate,
            expected_receipts=aed(receipts), expected_payments=aed(payments),
            net_cash_flow=aed(receipts - payments), method="direct"
        ))

# ─────────────────────────────────────────────────────────────────────────────
# CREDIT CONTROL RECORDS
# ─────────────────────────────────────────────────────────────────────────────
CC_REC = [
    ("Al Noor Trading LLC",100000,24800,"active",None,0),
    ("Emirates Hospitality Group",75000,4987.50,"active",None,0),
    ("Dubai Star Electronics",200000,50400,"active",None,0),
    ("Gulf Medical Supplies FZE",150000,23625,"active",None,0),
    ("Horizon Contracting LLC",80000,68250,"watch",(datetime.utcnow()+timedelta(days=7)).strftime("%Y-%m-%d"),0),
    ("Royal Palm Hotels",120000,0,"active",None,0),
    ("Blue Ocean Shipping",60000,2730,"active",None,0),
    ("Falcon Security Services",50000,0,"active",None,0),
    ("Summit Engineering Works",100000,6562.50,"active",None,0),
    ("Pearl of Arabia Retail",40000,7200,"active",None,0),
]
for cname, climit, outstanding, cstatus, ptp, bd_prov in CC_REC:
    exists = db.query(CreditControlRecord).filter(
        CreditControlRecord.company_id == CID,
        CreditControlRecord.customer_name == cname
    ).first()
    if not exists:
        db.add(CreditControlRecord(
            id=uid(), company_id=CID, customer_name=cname,
            credit_limit=aed(climit), outstanding_amount=aed(outstanding),
            credit_status=cstatus, promise_to_pay=ptp, bad_debt_provision=aed(bd_prov)
        ))

# ─────────────────────────────────────────────────────────────────────────────
# EXCEPTION EVENTS
# ─────────────────────────────────────────────────────────────────────────────
EX_DATA = [
    ("vat","missing_trn","medium","INV-2026-010","Customer TRN missing on INV-2026-010","open"),
    ("vat","missing_trn","medium","INV-2026-017","Customer TRN missing on INV-2026-017","open"),
    ("payroll","iban_missing","high","EMP-017","IBAN not validated for WPS — EMP-017","open"),
    ("invoices","duplicate_ref","low","INV-2026-019","Possible duplicate PO reference","open"),
    ("bank","unmatched_txn","medium","BSL-2026-008","Salary payment not matched to journal","resolved"),
    ("inventory","low_stock","low","PRD-013","A4 Paper reorder level reached","open"),
]
for mod, cat, sev, src, msg, status in EX_DATA:
    exists = db.query(ExceptionEvent).filter(
        ExceptionEvent.company_id == CID,
        ExceptionEvent.source_record == src,
        ExceptionEvent.category == cat
    ).first()
    if not exists:
        db.add(ExceptionEvent(
            id=uid(), company_id=CID, module=mod, category=cat,
            severity=sev, source_record=src, message=msg, status=status
        ))

# ─────────────────────────────────────────────────────────────────────────────
# MONTH END CLOSE
# ─────────────────────────────────────────────────────────────────────────────
ME_ITEMS = [
    ("2026-04","Bank reconciliation complete","done","Finance"),
    ("2026-04","Accounts receivable aging reviewed","done","Finance"),
    ("2026-04","Accounts payable aging reviewed","done","Finance"),
    ("2026-04","Prepayments and accruals posted","done","Finance"),
    ("2026-04","Payroll posted to ledger","done","HR/Finance"),
    ("2026-04","Depreciation posted","done","Finance"),
    ("2026-04","VAT return filed","done","Finance"),
    ("2026-04","Trial balance reviewed","done","Finance"),
    ("2026-05","Bank reconciliation complete","in_progress","Finance"),
    ("2026-05","Accounts receivable aging reviewed","open","Finance"),
    ("2026-05","Accounts payable aging reviewed","open","Finance"),
    ("2026-05","Prepayments and accruals posted","open","Finance"),
    ("2026-05","Payroll posted to ledger","done","HR/Finance"),
    ("2026-05","Depreciation posted","open","Finance"),
    ("2026-05","VAT return filed","open","Finance"),
    ("2026-05","Trial balance reviewed","open","Finance"),
]
for period_m, item, status, owner in ME_ITEMS:
    exists = db.query(MonthEndCloseRecord).filter(
        MonthEndCloseRecord.company_id == CID,
        MonthEndCloseRecord.period == period_m,
        MonthEndCloseRecord.checklist_item == item
    ).first()
    if not exists:
        db.add(MonthEndCloseRecord(
            id=uid(), company_id=CID, period=period_m, checklist_item=item,
            status=status, owner=owner, locked=(period_m == "2026-04")
        ))

# ─────────────────────────────────────────────────────────────────────────────
# ACCRUALS / PREPAYMENTS
# ─────────────────────────────────────────────────────────────────────────────
ACC_DATA = [
    ("accrual","ACC-001","Office Rent Accrual Q2",42000,14000),
    ("accrual","ACC-002","Salary Accrual May 2026",85000,85000),
    ("prepayment","PPM-001","Insurance Premium Prepaid",24000,2000),
    ("prepayment","PPM-002","Software Licenses Prepaid",36000,3000),
    ("prepayment","PPM-003","Annual Maintenance Contract",18000,1500),
]
for rtype, ref, desc, total, monthly in ACC_DATA:
    exists = db.query(AccrualPrepaymentRecord).filter(
        AccrualPrepaymentRecord.company_id == CID,
        AccrualPrepaymentRecord.reference == ref
    ).first()
    if not exists:
        db.add(AccrualPrepaymentRecord(
            id=uid(), company_id=CID, record_type=rtype, reference=ref,
            description=desc, total_amount=aed(total), monthly_amount=aed(monthly),
            reversal_day=1, status="active"
        ))

# ─────────────────────────────────────────────────────────────────────────────
# APPROVAL MATRIX
# ─────────────────────────────────────────────────────────────────────────────
AM_DATA = [
    ("invoices",0,10000,"finance_manager","Finance"),
    ("invoices",10000,50000,"finance_director","Finance"),
    ("invoices",50000,9999999,"ceo","Executive"),
    ("payments",0,5000,"finance_manager","Finance"),
    ("payments",5000,25000,"finance_director","Finance"),
    ("payments",25000,9999999,"ceo","Executive"),
    ("purchase",0,20000,"procurement_manager","Procurement"),
    ("purchase",20000,9999999,"finance_director","Finance"),
]
for mod, min_amt, max_amt, role, dept in AM_DATA:
    exists = db.query(ApprovalMatrixRecord).filter(
        ApprovalMatrixRecord.company_id == CID,
        ApprovalMatrixRecord.module == mod,
        ApprovalMatrixRecord.approver_role == role
    ).first()
    if not exists:
        db.add(ApprovalMatrixRecord(
            id=uid(), company_id=CID, module=mod, min_amount=aed(min_amt),
            max_amount=aed(max_amt), approver_role=role, department=dept
        ))

# ─────────────────────────────────────────────────────────────────────────────
# COMMIT ALL
# ─────────────────────────────────────────────────────────────────────────────
db.commit()
db.close()

print("✓ Seed complete — all tables populated with real-time test data")
print("  Tables seeded:")
print("  customers (20)  suppliers (20)  products (20)  employees (20)")
print("  sales invoices (20)  purchase invoices (20)  vouchers (20)")
print("  payments (5+)  receipts (5+)  journal entries (10)")
print("  payroll run (1 run × 20 employees)  bank statement lines (20)")
print("  stock movements (10)  warehouses (3)  VAT returns (4)")
print("  corporate tax returns (2)  fixed assets (10)  cost centers (8)")
print("  budget records (8)  cash flow forecasts (5)  credit control (10)")
print("  exception events (6)  month-end close (16)  accruals/prepayments (5)")
print("  approval matrix (8)")
