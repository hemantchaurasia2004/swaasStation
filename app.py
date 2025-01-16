from flask import Flask, send_file, jsonify, request, make_response
import qrcode
from io import BytesIO
import uuid
from datetime import datetime, timedelta
from reportlab.pdfgen import canvas
from reportlab.lib.pagesizes import letter
from reportlab.lib.utils import ImageReader
from pymongo import MongoClient
from dotenv import load_dotenv
import os

# Load environment variables
load_dotenv()

app = Flask(__name__)

# MongoDB Atlas connection
MONGO_URI = os.getenv('MONGODB_URI')
MASTER_IP = os.getenv('MASTER_IP', '192.168.137.1')

def init_mongodb():
    try:
        # Add TLS/SSL certificates and additional options
        client = MongoClient(MONGO_URI, 
                           tls=True, 
                           tlsAllowInvalidCertificates=True,
                           retryWrites=True,
                           serverSelectionTimeoutMS=5000)
        
        db = client.coupon_system
        
        # Test connection
        client.admin.command('ping')
        print("Successfully connected to MongoDB!")
        
        # Initialize collections
        coupons_collection = db.coupons
        config_collection = db.config
        
        # Initialize coupon limit if not exists
        if not config_collection.find_one({'_id': 'coupon_limit'}):
            config_collection.insert_one({
                '_id': 'coupon_limit',
                'limit': 150,
                'current_count': 0
            })
            
        return db, coupons_collection, config_collection
        
    except Exception as e:
        print(f"Error connecting to MongoDB: {e}")
        return None, None, None




# Initialize MongoDB connections
db, coupons_collection, config_collection = init_mongodb()


def is_ip_allowed(ip):
    """Check if IP has never generated a coupon before or is master IP"""
    return ip == MASTER_IP or not coupons_collection.find_one({'generating_ip': ip})

def can_generate_coupon():
    """Check if the coupon limit has been reached"""
    config = config_collection.find_one({'_id': 'coupon_limit'})
    return config['current_count'] < config['limit']

def increment_coupon_count():
    """Increment the current coupon count"""
    config_collection.update_one(
        {'_id': 'coupon_limit'},
        {'$inc': {'current_count': 1}}
    )

def generate_qr_code(data):
    """Generate QR code and return the image"""
    qr = qrcode.QRCode(version=1, box_size=10, border=5)
    qr.add_data(data)
    qr.make(fit=True)
    img = qr.make_image(fill_color="black", back_color="white")
    
    img_buffer = BytesIO()
    img.save(img_buffer)
    img_buffer.seek(0)
    return img_buffer

def create_coupon_pdf(coupon_id, qr_img_buffer, expiry_date):
    """Create a PDF with the coupon and QR code"""
    pdf_buffer = BytesIO()
    c = canvas.Canvas(pdf_buffer, pagesize=letter)
    width, height = letter
    
    # Add decorative border
    c.setStrokeColorRGB(0.8, 0.4, 0)
    c.setDash(6, 3)
    c.rect(50, 50, width-100, height-100)
    
    # Add company name
    c.setFont("Helvetica-Bold", 36)
    c.setFillColorRGB(0.6, 0.3, 0)
    c.drawCentredString(width/2, height-100, "Swaad Station")
    
    # Add "DISCOUNT COUPON" text
    c.setFont("Helvetica-Bold", 24)
    c.setFillColorRGB(0, 0, 0)
    c.drawCentredString(width/2, height-150, "10% OFF COUPON")
    
    # Add description
    c.setFont("Helvetica", 14)
    c.drawCentredString(width/2, height-180, "Present this QR code at checkout")
    
    # Add QR code
    qr_img = ImageReader(qr_img_buffer)
    c.drawImage(qr_img, width/2-100, height/2-100, 200, 200)
    
    # Add coupon details
    c.setFont("Helvetica", 12)
    c.drawCentredString(width/2, height/2-150, f"Coupon ID: {coupon_id}")
    c.drawCentredString(width/2, height/2-170, 
                       f"Valid until: {expiry_date.strftime('%Y-%m-%d %H:%M:%S')}")
    
    # Add terms and conditions
    c.setFont("Helvetica", 10)
    c.drawCentredString(width/2, 100, "Terms & Conditions:")
    c.drawCentredString(width/2, 80, "One-time use only. Cannot be combined with other offers.")
    c.drawCentredString(width/2, 60, "Valid only at Swaad Station")
    
    c.save()
    pdf_buffer.seek(0)
    return pdf_buffer

@app.route('/test_db')
def test_db():
    """Test database connection"""
    try:
        config = config_collection.find_one({'_id': 'coupon_limit'})
        return jsonify({
            'status': 'success',
            'data': {
                'limit': config['limit'],
                'current_count': config['current_count']
            }
        })
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        })


@app.route('/generate_coupon')
def generate_coupon():
    """Generate a new coupon and return as PDF"""
    try:
        # Check for an existing cookie
        if request.cookies.get('coupon_generated'):
            return jsonify({
                'error': 'Coupon generation not allowed',
                'message': 'You have already generated a coupon. Only one coupon per device is allowed.'
            }), 403
        
        client_ip = request.remote_addr
        
        # Check if coupon limit reached
        if not can_generate_coupon() and client_ip != MASTER_IP:
            return jsonify({
                'error': 'Coupon generation not allowed',
                'message': 'Coupon limit reached. Please try again later.'
            }), 403
        
        # Generate coupon
        coupon_id = str(uuid.uuid4())[:8]
        expiry_date = datetime.now() + timedelta(days=1)
        
        # Store coupon in MongoDB
        coupon_data = {
            '_id': coupon_id,
            'valid': True,
            'expiry_date': expiry_date,
            'discount': '10%',
            'used': False,
            'generating_ip': client_ip,
            'generated_by_master': client_ip == MASTER_IP,
            'created_at': datetime.now()
        }
        
        coupons_collection.insert_one(coupon_data)
        
        # Increment coupon count if not master IP
        if client_ip != MASTER_IP:
            increment_coupon_count()
        
        # Generate and return PDF
        qr_buffer = generate_qr_code(coupon_id)
        pdf_buffer = create_coupon_pdf(coupon_id, qr_buffer, expiry_date)
        
        # Set a cookie to prevent further generation
        response = make_response(send_file(
            pdf_buffer,
            mimetype='application/pdf',
            as_attachment=True,
            download_name=f'swaad_station_coupon_{coupon_id}.pdf'
        ))
        response.set_cookie('coupon_generated', 'true', max_age=24 * 60 * 60)  # 1-day expiration
        
        return response
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500
    
@app.route('/validate_coupon/<coupon_id>')
def validate_coupon(coupon_id):
    """Validate a coupon"""
    try:
        coupon = coupons_collection.find_one({'_id': coupon_id})
        
        if not coupon:
            return jsonify({'valid': False, 'message': 'Coupon not found'})
        
        if coupon['used']:
            return jsonify({'valid': False, 'message': 'Coupon already used'})
        
        if datetime.now() > coupon['expiry_date']:
            return jsonify({'valid': False, 'message': 'Coupon expired'})
        
        coupons_collection.update_one(
            {'_id': coupon_id},
            {'$set': {'used': True}}
        )
        
        return jsonify({
            'valid': True,
            'message': 'Valid coupon - 10% discount applied at Swaad Station',
            'discount': coupon['discount']
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/admin/reset_limit', methods=['POST'])
def reset_limit():
    """Reset or update the coupon limit (only accessible from master IP)"""
    try:
        if request.remote_addr != MASTER_IP:
            return jsonify({'error': 'Unauthorized'}), 403
        
        new_limit = request.json.get('limit', 150)
        
        config_collection.update_one(
            {'_id': 'coupon_limit'},
            {'$set': {
                'limit': new_limit,
                'current_count': 0
            }}
        )
        
        return jsonify({
            'message': f'Coupon limit reset to {new_limit}',
            'new_limit': new_limit
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

@app.route('/admin/stats')
def admin_stats():
    """Get statistics about generated coupons (only accessible from master IP)"""
    try:
        if request.remote_addr != MASTER_IP:
            return jsonify({'error': 'Unauthorized'}), 403
        
        config = config_collection.find_one({'_id': 'coupon_limit'})
        total_coupons = coupons_collection.count_documents({})
        master_generated = coupons_collection.count_documents({'generated_by_master': True})
        used_coupons = coupons_collection.count_documents({'used': True})
        unique_users = len(coupons_collection.distinct('generating_ip'))
        
        return jsonify({
            'total_coupons_generated': total_coupons,
            'master_generated_coupons': master_generated,
            'user_generated_coupons': total_coupons - master_generated,
            'used_coupons': used_coupons,
            'unique_users': unique_users,
            'current_limit': config['limit'],
            'remaining_coupons': config['limit'] - config['current_count']
        })
        
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': str(e)
        }), 500

if __name__ == '__main__':
    app.run(debug=True)
