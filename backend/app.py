# app.py
from flask import Flask, jsonify , request
from flask_socketio import SocketIO
from flask_cors import CORS
from pymongo import MongoClient
import requests
import logging
import time
from datetime import datetime, timedelta

logging.basicConfig(
    level=logging.DEBUG,
    format='%(asctime)s [%(levelname)s] %(message)s'
)
logger = logging.getLogger(__name__)

app = Flask(__name__)
CORS(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# MongoDB connection
try:
    logger.info("Connecting to MongoDB...")
    client = MongoClient('mongodb://mongodb:27017/')
    db = client['dam_database']
    dam_collection = db['dam_data']
    historical_collection = db['historical_data']
    logger.info("MongoDB connected successfully")
except Exception as e:
    logger.error(f"MongoDB connection error: {e}")
    raise

def process_dam_data(raw_data):
    """ประมวลผลข้อมูลเขื่อน"""
    try:
        processed_dams = []
        regions_data = raw_data.get('data', [])

        for region in regions_data:
            region_name = region.get('region', '')
            dams = region.get('dam', [])
            
            for dam in dams:
                try:
                    processed_dam = {
                        'id': str(dam.get('id', '')),
                        'name': dam.get('name', ''),
                        'region': region_name,
                        'owner': dam.get('owner', ''),
                        'capacity': float(dam.get('capacity', 0)),
                        'storage': float(dam.get('storage', 0)),
                        'active_storage': float(dam.get('active_storage', 0)),
                        'dead_storage': float(dam.get('dead_storage', 0)),
                        'volume': float(dam.get('volume', 0)),
                        'percent_storage': float(dam.get('percent_storage', 0)),
                        'inflow': float(dam.get('inflow', 0)),
                        'outflow': float(dam.get('outflow', 0)) if dam.get('outflow') is not None else 0,
                        'date': raw_data.get('date', ''),
                        'updated_at': datetime.now(),
                        'status': get_dam_status(float(dam.get('percent_storage', 0)))
                    }
                    processed_dams.append(processed_dam)
                    logger.debug(f"Processed dam: {processed_dam['name']} ({processed_dam['region']})")
                
                except Exception as e:
                    logger.error(f"Error processing dam {dam.get('name', 'unknown')}: {e}")
                    continue

        return processed_dams

    except Exception as e:
        logger.error(f"Error in process_dam_data: {e}")
        return None

def get_dam_status(percent):
    """กำหนดสถานะตามเปอร์เซ็นต์น้ำ"""
    if percent >= 100:
        return "น้ำล้น"
    elif percent >= 80:
        return "น้ำมาก"
    elif percent >= 50:
        return "น้ำปกติ"
    elif percent >= 30:
        return "น้ำน้อย"
    else:
        return "น้ำวิกฤต"

def fetch_and_store_data():
    """ดึงและบันทึกข้อมูล"""
    while True:
        try:
            logger.info("Fetching data from API...")
            response = requests.get(
                'https://app.rid.go.th/reservoir/api/dam/public',
                timeout=30
            )
            response.raise_for_status()
            
            raw_data = response.json()
            total_dams = raw_data.get('total', 0)
            logger.info(f"Received data for {total_dams} dams")
            
            processed_data = process_dam_data(raw_data)
            if not processed_data:
                logger.error("Failed to process data")
                continue

            # บันทึกข้อมูลลง MongoDB
            for dam in processed_data:
                try:
                    # บันทึกข้อมูลปัจจุบัน
                    dam_collection.update_one(
                        {'id': dam['id']},
                        {'$set': dam},
                        upsert=True
                    )
                    
                    # บันทึกประวัติ
                    historical_data = dam.copy()
                    historical_data['dam_id'] = historical_data['id']
                    historical_data['recorded_at'] = datetime.now()
                    historical_collection.insert_one(historical_data)
                    
                    logger.debug(f"Saved data for dam: {dam['name']}")

                except Exception as e:
                    logger.error(f"Error saving dam {dam.get('name')}: {e}")
                    continue

            # ส่งข้อมูลผ่าน WebSocket
            socketio.emit('dam_data_update', processed_data)
            logger.info(f"Successfully processed and stored {len(processed_data)} dams")

        except requests.RequestException as e:
            logger.error(f"API request error: {e}")
        except Exception as e:
            logger.error(f"Unexpected error: {e}")

        time.sleep(300)  # รอ 5 วินาที
        
@app.route('/')
def home():
    return jsonify({
        'status': 'ok',
        'message': 'Water Level Monitoring API'
    })

# เพิ่ม error handler
@app.errorhandler(404)
def not_found(e):
    return jsonify({
        'status': 'error',
        'message': 'Route not found'
    }), 404

# ให้ CORS ทำงานกับทุก route
CORS(app, resources={r"/*": {"origins": "*"}})
@app.route('/api/status')
def api_status():
    """ตรวจสอบสถานะระบบ"""
    try:
        dam_count = dam_collection.count_documents({})
        historical_count = historical_collection.count_documents({})
        latest_update = dam_collection.find_one({}, sort=[('updated_at', -1)])
        
        return jsonify({
            'status': 'ok',
            'mongodb_status': 'connected',
            'dam_count': dam_count,
            'historical_count': historical_count,
            'latest_update': latest_update['updated_at'] if latest_update else None
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

@app.route('/api/dams')
def get_dams():
    """ดึงข้อมูลเขื่อนทั้งหมด"""
    try:
        dams = list(dam_collection.find({}, {'_id': 0}))
        return jsonify({
            'status': 'ok',
            'count': len(dams),
            'data': dams
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500

from datetime import datetime, timedelta

@app.route('/api/dams/<dam_id>/history')
def get_dam_history(dam_id):
    try:
        # รับค่าวันที่จาก query parameters
        start_date = request.args.get('start')
        end_date = request.args.get('end')

        # แปลงวันที่เป็น datetime object
        start_datetime = datetime.fromisoformat(start_date) if start_date else datetime.now() - timedelta(days=7)
        end_datetime = datetime.fromisoformat(end_date) if end_date else datetime.now()

        # ค้นหาข้อมูลใน MongoDB
        history = list(historical_collection.find(
            {
                'dam_id': dam_id,  # กรองด้วย dam_id
                'recorded_at': {
                    '$gte': start_datetime,  # ช่วงเวลาที่ระบุ
                    '$lte': end_datetime
                }
            },
            {
                '_id': 0,  # ไม่แสดง _id
                'volume': 1,  # ดึงเฉพาะฟิลด์ที่ต้องการ
                'percent_storage': 1,
                'recorded_at': 1
            }
        ).sort('recorded_at', 1))  # เรียงลำดับข้อมูลตาม recorded_at

        # ส่งข้อมูลกลับไปยัง frontend
        return jsonify({
            'status': 'ok',
            'data': history
        })
    except Exception as e:
        # หากเกิดข้อผิดพลาด
        return jsonify({
            'status': 'error',
            'error': str(e)
        }), 500


if __name__ == '__main__':
    # เริ่ม thread สำหรับดึงข้อมูล
    import threading
    fetch_thread = threading.Thread(target=fetch_and_store_data)
    fetch_thread.daemon = True
    fetch_thread.start()
    logger.info("Data fetching thread started")
    
    # รัน Flask app
    socketio.run(app, host='0.0.0.0', port=5000, debug=True)