[app]
title = Spark Billing
package.name = sparkbilling
package.domain = org.spark
source.dir = .
source.include_exts = py,png,jpg,kv,db
version = 1.0.1
requirements = python3,kivy==2.3.0,fpdf,pillow,pyjnius,android,setuptools,sh

# Android permissions (Storage for PDF/JPG export, Bluetooth for POS)
android.permissions = WRITE_EXTERNAL_STORAGE, READ_EXTERNAL_STORAGE, MANAGE_EXTERNAL_STORAGE, BLUETOOTH, BLUETOOTH_ADMIN, BLUETOOTH_CONNECT, BLUETOOTH_SCAN, INTERNET

# Orientation
orientation = portrait

# Icon & Presplash
icon.filename = %(source.dir)s/logo.png

# Android API settings
android.api = 33
android.minapi = 21
android.ndk = 25b
android.sdk = 33
android.accept_sdk_license = True

# Architectures (arm64-v8a is mandatory for Play Store, armeabi-v7a for older phones)
android.archs = arm64-v8a, armeabi-v7a

# Allow using jnius for Android system calls
android.pyscaffold = False
android.entrypoint = main.py
android.enable_androidx = True

[buildozer]
log_level = 2
warn_on_root = 1
bin_dir = ./bin
