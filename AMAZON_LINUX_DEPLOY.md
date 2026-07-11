# دليل التشغيل على Amazon Linux 2 / Amazon Linux 2023

## المتطلبات المسبقة

تأكد من أن الخادم يحتوي على Python 3.8+ ومتاح:

```bash
python3 --version
```

---

## الخطوة 1: تحديث النظام وتثبيت المتطلبات

```bash
# تحديث النظام
sudo yum update -y    # Amazon Linux 2
# أو
sudo dnf update -y    # Amazon Linux 2023

# تثبيت Python 3 إذا لم يكن موجود
sudo yum install -y python3 python3-pip git
# أو
sudo dnf install -y python3 python3-pip git
```

---

## الخطوة 2: استنساخ المشروع

```bash
cd /home/ubuntu
git clone https://github.com/Madr50/tg-dm-sender.git
cd tg-dm-sender
```

---

## الخطوة 3: إنشاء ملف config.json

```bash
cat > config.json << 'EOF'
{
  "api_id": "YOUR_API_ID",
  "api_hash": "YOUR_API_HASH",
  "phone": "+YOUR_PHONE_NUMBER"
}
EOF
```

> **مهم**: استبدل القيم أعلاه ببياناتك من my.telegram.org

---

## الخطوة 4: تثبيت المكتبات

```bash
pip3 install -r requirements.txt
```

---

## الخطوة 5: تسجيل الدخول (لأول مرة فقط)

```bash
# تشغيل الكود لطلب OTP
python3 app.py &
sleep 5
```

> سيتم طلب رمز التحقق من Telegram في المخرجات

---

## الخطوة 6: إيقاف العملية المؤقتة

```bash
# إيقاف العملية التي تعمل في الخلفية
pkill -f "python3 app.py"
# أو
kill $(lsof -ti:5000)
```

---

## الخطوة 7: تسجيل الدخول كـ systemd Service (موصى به)

```bash
# نسخ ملف الخدمة
sudo cp tg-dm-sender.service /etc/systemd/system/

# إعادة تحميل systemd
sudo systemctl daemon-reload

# تمكين الخدمة لتبدأ تلقائياً عند إعادة التشغيل
sudo systemctl enable tg-dm-sender.service

# تشغيل الخدمة
sudo systemctl start tg-dm-sender.service

# التحقق من حالة الخدمة
sudo systemctl status tg-dm-sender.service
```

---

## الخطوة 8: أوامر الإدارة المفيدة

```bash
# عرض الحالة
sudo systemctl status tg-dm-sender.service

# عرض السجلات
sudo journalctl -u tg-dm-sender.service -f

# إعادة التشغيل
sudo systemctl restart tg-dm-sender.service

# إيقاف
sudo systemctl stop tg-dm-sender.service

# تعطيل البدء التلقائي
sudo systemctl disable tg-dm-sender.service
```

---

## بديل: التشغيل المباشر (بدون systemd)

```bash
# تشغيل في الخلفية مع حفظ السجلات
cd /home/ubuntu/tg-dm-sender
nohup python3 app.py > app.log 2>&1 &

# عرض رقم العملية
echo $!

# إيقاف لاحقاً
kill $(lsof -ti:5000)
# أو
pkill -f "python3 app.py"
```

---

## بديل 2: التشغيل مع screen (للمراقبة التفاعلية)

```bash
# تثبيت screen
sudo yum install -y screen

# إنشاء جلسة screen
screen -S tg-dm-sender

# تشغيل التطبيق
python3 app.py

# للخروج من screen مع ترك العملية تعمل: Ctrl+A ثم D

# للعودة لاحقاً
screen -r tg-dm-sender
```

---

## إعداد الـ Watchdog (إعادة تشغيل تلقائي)

```bash
# إعطاء صلاحيات التنفيذ
chmod +x /home/ubuntu/tg-dm-sender/watchdog.sh

# إضافة للـ crontab (تشغيل كل دقيقة)
crontab -e

# أضف هذا السطر:
* * * * * /home/ubuntu/tg-dm-sender/watchdog.sh >> /home/ubuntu/tg-dm-sender/watchdog.log 2>&1

# حفظ والخروج (Ctrl+X, Y, Enter في nano)
```

---

## فتح المنفذ في Amazon Security Group

1. اذهب إلى **EC2 Dashboard**
2. اختر الـ **Instance**
3. اذهب إلى تبويب **Security**
4. اختر **Security Group**
5. أضف **Inbound Rule**:
   - Type: Custom TCP
   - Port Range: 5000
   - Source: 0.0.0.0/0 (أو IP محدد)

---

## التحقق من التشغيل

```bash
# التحقق من أن التطبيق يعمل
curl http://localhost:5000/health

# أو من المتصفح
http://YOUR_SERVER_IP:5000
```

---

## حل مشاكل SQLite Lock (المشكلة الرئيسية)

إذا واجهت مشكلة **database locked** مرة أخرى:

```bash
# 1. أوقف التطبيق
sudo systemctl stop tg-dm-sender.service

# 2. احذف ملفات القفل
cd /home/ubuntu/tg-dm-sender
rm -f *.sqlite-wal *.sqlite-shm

# 3. أعد التشغيل
sudo systemctl start tg-dm-sender.service
```

> **ملاحظة**: الكود المحدث (V4) يتعامل تلقائياً مع ملفات القفل قبل الاتصال بـ Telegram، مما يمنع المشكلة من الحدوث.
