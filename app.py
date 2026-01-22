from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from werkzeug.security import generate_password_hash, check_password_hash
from config import Config, db
from google.cloud.firestore import Increment  # Add this import
import uuid
# from flask import Flask, render_template, request, redirect, url_for, flash, session, jsonify
from datetime import datetime
from firebase_admin import firestore
from google.cloud.firestore_v1 import Increment
import json
from flask import request, jsonify
from flask_mail import Mail, Message
import secrets
from datetime import datetime, timezone, timedelta 
import hashlib
from google.cloud.firestore_v1 import FieldFilter
from flask import Flask, render_template
from flask_socketio import SocketIO, emit
import subprocess
import threading
import traceback
# Add these imports at the top with your other imports
from flask_socketio import SocketIO, emit, join_room, leave_room
from datetime import datetime, timedelta
import time
import os
from werkzeug.utils import secure_filename
import uuid
import urllib.parse
from flask import send_file


### Step 3: Create Firestore Config Document
# Initialize Flask app and SocketIO

app = Flask(__name__)
app.config.from_object(Config)
mail = Mail(app)
socketio = SocketIO(app, cors_allowed_origins="*")

# Add these configurations near the top of your file, after app initialization
UPLOAD_FOLDER = os.path.join(app.static_folder, 'uploads', 'pdfs', 'topics')
ALLOWED_EXTENSIONS = {'pdf'}
MAX_FILE_SIZE = 10 * 1024 * 1024  # 10MB

# Ensure upload folder exists
os.makedirs(UPLOAD_FOLDER, exist_ok=True)

# Helper function for file validation
def allowed_file(filename):
    return '.' in filename and filename.rsplit('.', 1)[1].lower() in ALLOWED_EXTENSIONS

# ...rest of your code (User class, routes, etc.)...

class User:
    def __init__(self, user_id, username, email, role):
        self.id = user_id
        self.username = username
        self.email = email
        self.role = role

MAINTENANCE = os.getenv("MAINTENANCE", "false") == "true"

@app.before_request
def block_requests():
    if MAINTENANCE:
        return {"error": "Service temporarily disabled"}, 503


@app.template_filter('get_animal_emoji')
def get_animal_emoji(animal_id):
    """Template filter to get animal emoji"""
    animal_emojis = {
        'cat': '🐱', 'dog': '🐶', 'fox': '🦊', 'bear': '🐻', 'panda': '🐼', 'koala': '🐨',
        'lion': '🦁', 'tiger': '🐯', 'wolf': '🐺', 'rabbit': '🐰', 'monkey': '🐵', 'elephant': '🐘',
        'penguin': '🐧', 'owl': '🦉', 'turtle': '🐢', 'unicorn': '🦄'
    }
    return animal_emojis.get(animal_id, '🐱')

@app.route('/update-avatar', methods=['POST'])
def update_avatar():
    print(f"DEBUG: Update avatar called")  # Debug line
    
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please log in'}), 401
    
    try:
        data = request.get_json()
        avatar_type = data.get('avatar_type')
        avatar_id = data.get('avatar_id')
        
        print(f"DEBUG: Avatar type: {avatar_type}, Avatar ID: {avatar_id}")  # Debug line
        
        if not avatar_type or not avatar_id:
            return jsonify({'success': False, 'message': 'Invalid avatar data'}), 400
        
        # Update user document
        user_ref = db.collection('users').document(session['user_id'])
        update_data = {
            'avatar_type': avatar_type,
            'avatar_id': avatar_id,
            'updated_at': datetime.now()
        }
        
        user_ref.update(update_data)
        print(f"DEBUG: User avatar updated successfully")  # Debug line
        
        return jsonify({'success': True, 'message': 'Avatar updated successfully'})
        
    except Exception as e:
        print(f"ERROR updating avatar: {e}")
        return jsonify({'success': False, 'message': 'Error updating avatar'}), 500

@app.route('/')
def home():
    # Get all subjects from teachers
    subjects = []
    try:
        subjects_ref = db.collection('subjects')
        docs = subjects_ref.stream()
        
        for doc in docs:
            subject_data = doc.to_dict()
            subject_data['id'] = doc.id
            
            # Check enrollment status if user is logged in
            if 'user_id' in session and session.get('role') == 'student':
                subject_data['is_enrolled'] = check_enrollment(session['user_id'], doc.id)
            else:
                subject_data['is_enrolled'] = False
            
            subjects.append(subject_data)
    except Exception as e:
        print(f"Error fetching subjects: {e}")
    
    # Pass session information to template
    username = session.get('username')
    role = session.get('role')
    
    return render_template('home.html', 
                         subjects=subjects, 
                         username=username, 
                         role=role)

@app.route('/signup', methods=['GET', 'POST'])
def signup():
    if request.method == 'POST':
        username = request.form['username']
        email = request.form['email']
        password = request.form['password']
        confirm_password = request.form['confirm_password']
        role = request.form['role']
        
        # Validation
        if not username or not email or not password or not confirm_password:
            flash('Please fill in all fields.')
            return render_template('signup.html')
        
        if len(username) < 3:
            flash('Username must be at least 3 characters long.')
            return render_template('signup.html')
        
        if len(password) < 6:
            flash('Password must be at least 6 characters long.')
            return render_template('signup.html')
        
        if password != confirm_password:
            flash('Passwords do not match.')
            return render_template('signup.html')
        
        # Check if user already exists
        users_ref = db.collection('users')
        existing_user = users_ref.where('email', '==', email).get()
        
        if existing_user:
            flash('Email already exists. Please use a different email.')
            return render_template('signup.html')
        
        # Check if username already exists
        existing_username = users_ref.where('username', '==', username).get()
        if existing_username:
            flash('Username already exists. Please choose a different username.')
            return render_template('signup.html')
        
        try:
            # Hash password
            hashed_password = generate_password_hash(password)
            
            # Generate email confirmation token
            confirmation_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(confirmation_token.encode()).hexdigest()
            
            # Set token expiration (24 hours from now)
            current_time = datetime.now(timezone.utc)
            expires_at = current_time + timedelta(hours=24)
            
            # Create pending user document (not activated yet)
            user_data = {
                'username': username,
                'email': email,
                'password': hashed_password,
                'role': role,
                'is_verified': False,
                'verification_token_hash': token_hash,
                'token_expires_at': expires_at,
                'created_at': current_time
            }
            
            # Save user to 'pending_users' collection instead of 'users'
            pending_users_ref = db.collection('pending_users')
            doc_ref = pending_users_ref.add(user_data)
            
            # Send confirmation email
            confirmation_url = url_for('confirm_email', token=confirmation_token, _external=True)
            
            msg = Message(
                'Welcome to Quizera - Confirm Your Email',
                recipients=[email],
                html=f'''
                <html>
                <body>
                    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                        <div style="background-color: #2563eb; color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0;">
                            <h1 style="margin: 0; font-size: 28px;">Welcome to Quizera!</h1>
                        </div>
                        <div style="padding: 30px; border: 1px solid #e5e5e5; border-radius: 0 0 10px 10px;">
                            <h2 style="color: #2563eb;">Confirm Your Email Address</h2>
                            <p>Hello {username},</p>
                            <p>Thank you for signing up for Quizera! To complete your registration and activate your account, please click the button below to confirm your email address:</p>
                            
                            <div style="text-align: center; margin: 30px 0;">
                                <a href="{confirmation_url}" 
                                   style="background-color: #2563eb; color: white; padding: 15px 30px; 
                                          text-decoration: none; border-radius: 8px; display: inline-block;
                                          font-weight: bold; font-size: 16px;">
                                    Confirm Email Address
                                </a>
                            </div>
                            
                            <p>Or copy and paste this link into your browser:</p>
                            <p style="word-break: break-all; background-color: #f8f9fa; padding: 10px; border-radius: 5px; font-family: monospace;">
                                <a href="{confirmation_url}">{confirmation_url}</a>
                            </p>
                            
                            <div style="background-color: #fff3cd; border: 1px solid #ffeaa7; padding: 15px; border-radius: 5px; margin: 20px 0;">
                                <p style="margin: 0; color: #856404;"><strong>Important:</strong> This confirmation link will expire in 24 hours. If you don't confirm your email within this time, you'll need to sign up again.</p>
                            </div>
                            
                            <p>Once you confirm your email, you'll be able to:</p>
                            <ul style="color: #555;">
                                <li>Access your personalized dashboard</li>
                                <li>{"Create and manage quizzes and subjects" if role == "teacher" else "Enroll in subjects and take quizzes"}</li>
                                <li>Track your progress and achievements</li>
                            </ul>
                            
                            <p>If you didn't create an account with Quizera, please ignore this email.</p>
                            
                            <hr style="margin: 30px 0; border: 1px solid #e5e5e5;">
                            <p style="color: #666; font-size: 12px; text-align: center;">
                                This is an automated message from Quizera. Please do not reply to this email.<br>
                                Need help? Contact our support team.
                            </p>
                        </div>
                    </div>
                </body>
                </html>
                '''
            )
            
            mail.send(msg)
            flash('Account created successfully! Please check your email and click the confirmation link to activate your account.')
            return redirect(url_for('login'))
            
        except Exception as e:
            print(f"Error creating account: {e}")
            flash(f'Error creating account: {e}')
            return render_template('signup.html')
    
    return render_template('signup.html')

# Add this new route for email confirmation
@app.route('/confirm-email/<token>')
def confirm_email(token):
    try:
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        
        # Find pending user with this token
        pending_users_ref = db.collection('pending_users')
        pending_docs = list(pending_users_ref.where('verification_token_hash', '==', token_hash).where('is_verified', '==', False).get())
        
        if not pending_docs:
            flash('Invalid or expired confirmation link. Please sign up again.')
            return redirect(url_for('signup'))
        
        pending_doc = pending_docs[0]
        pending_data = pending_doc.to_dict()
        
        # Check if token is expired
        current_time = datetime.now(timezone.utc)
        expires_at = pending_data['token_expires_at']
        
        # Handle timezone conversion
        if hasattr(expires_at, 'timestamp'):
            expires_at_dt = expires_at.replace(tzinfo=timezone.utc)
        elif isinstance(expires_at, datetime):
            if expires_at.tzinfo is None:
                expires_at_dt = expires_at.replace(tzinfo=timezone.utc)
            else:
                expires_at_dt = expires_at.astimezone(timezone.utc)
        else:
            expires_at_dt = datetime.fromisoformat(str(expires_at)).replace(tzinfo=timezone.utc)
        
        if current_time > expires_at_dt:
            # Delete expired pending user
            pending_doc.reference.delete()
            flash('Confirmation link has expired. Please sign up again.')
            return redirect(url_for('signup'))
        
        # Move user from pending_users to users collection
        user_data = {
            'username': pending_data['username'],
            'email': pending_data['email'],
            'password': pending_data['password'],
            'role': pending_data['role'],
            'is_verified': True,
            'created_at': pending_data['created_at'],
            'verified_at': current_time
        }
        
        # Add to users collection
        users_ref = db.collection('users')
        users_ref.add(user_data)
        
        # Delete from pending_users collection
        pending_doc.reference.delete()
        
        flash(f'Email confirmed successfully! Welcome to Quizera, {pending_data["username"]}! You can now log in.')
        return redirect(url_for('login'))
        
    except Exception as e:
        print(f"Error confirming email: {e}")
        flash('An error occurred during email confirmation. Please try again or contact support.')
        return redirect(url_for('signup'))

# Add a cleanup route for expired pending users (run this periodically)
@app.route('/admin/cleanup-pending-users', methods=['POST'])
def cleanup_pending_users():
    """Admin route to clean up expired pending user registrations"""
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    try:
        current_time = datetime.now(timezone.utc)
        
        # Delete expired pending users
        pending_users_ref = db.collection('pending_users')
        expired_docs = list(pending_users_ref.where('token_expires_at', '<', current_time).get())
        
        deleted_count = 0
        for doc in expired_docs:
            doc.reference.delete()
            deleted_count += 1
        
        return jsonify({
            'success': True, 
            'message': f'Cleaned up {deleted_count} expired pending registrations'
        })
        
    except Exception as e:
        return jsonify({
            'success': False, 
            'message': f'Error cleaning up pending users: {e}'
        }), 500

# Optional: Add a route to resend confirmation email
@app.route('/resend-confirmation', methods=['GET', 'POST'])
def resend_confirmation():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        
        if not email:
            flash('Please enter your email address.')
            return render_template('resend_confirmation.html')
        
        try:
            # Find pending user
            pending_users_ref = db.collection('pending_users')
            pending_docs = list(pending_users_ref.where('email', '==', email).where('is_verified', '==', False).get())
            
            if not pending_docs:
                flash('No pending registration found for this email address.')
                return render_template('resend_confirmation.html')
            
            pending_doc = pending_docs[0]
            pending_data = pending_doc.to_dict()
            
            # Generate new confirmation token
            confirmation_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(confirmation_token.encode()).hexdigest()
            
            # Update token expiration (24 hours from now)
            current_time = datetime.now(timezone.utc)
            expires_at = current_time + timedelta(hours=24)
            
            # Update pending user with new token
            pending_doc.reference.update({
                'verification_token_hash': token_hash,
                'token_expires_at': expires_at
            })
            
            # Send confirmation email
            confirmation_url = url_for('confirm_email', token=confirmation_token, _external=True)
            username = pending_data['username']
            role = pending_data['role']
            
            msg = Message(
                'Quizera - Confirmation Email Resent',
                recipients=[email],
                html=f'''
                <html>
                <body>
                    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                        <div style="background-color: #2563eb; color: white; padding: 20px; text-align: center; border-radius: 10px 10px 0 0;">
                            <h1 style="margin: 0; font-size: 28px;">Confirm Your Email</h1>
                        </div>
                        <div style="padding: 30px; border: 1px solid #e5e5e5; border-radius: 0 0 10px 10px;">
                            <p>Hello {username},</p>
                            <p>You requested a new confirmation email for your Quizera account. Please click the button below to confirm your email address:</p>
                            
                            <div style="text-align: center; margin: 30px 0;">
                                <a href="{confirmation_url}" 
                                   style="background-color: #2563eb; color: white; padding: 15px 30px; 
                                          text-decoration: none; border-radius: 8px; display: inline-block;
                                          font-weight: bold; font-size: 16px;">
                                    Confirm Email Address
                                </a>
                            </div>
                            
                            <p>This link will expire in 24 hours.</p>
                        </div>
                    </div>
                </body>
                </html>
                '''
            )
            
            mail.send(msg)
            flash('Confirmation email has been resent. Please check your email.')
            return redirect(url_for('login'))
            
        except Exception as e:
            print(f"Error resending confirmation: {e}")
            flash('An error occurred. Please try again later.')
    
    return render_template('resend_confirmation.html')

@app.route('/login', methods=['GET', 'POST'])
def login():
    if request.method == 'POST':
        email = request.form['email']
        password = request.form['password']
        
        # Find user by email
        users_ref = db.collection('users')
        user_docs = users_ref.where('email', '==', email).get()
        
        if not user_docs:
            # Check if user is in pending_users (not yet verified)
            pending_users_ref = db.collection('pending_users')
            pending_docs = list(pending_users_ref.where('email', '==', email).get())
            
            if pending_docs:
                flash('Please check your email and click the confirmation link to activate your account. <a href="/resend-confirmation" class="text-blue-600 hover:text-blue-800">Resend confirmation email</a>')
            else:
                flash('Invalid email or password.', 'error')
            return render_template('login.html')
        
        user_doc = user_docs[0]
        user_data = user_doc.to_dict()
        
        # Check if user is verified
        if not user_data.get('is_verified', True):  # Default to True for existing users
            flash('Please confirm your email address before logging in. <a href="/resend-confirmation" class="text-blue-600 hover:text-blue-800">Resend confirmation email</a>')
            return render_template('login.html')
        
        # Check password
        if check_password_hash(user_data['password'], password):
            # Store user info in session
            session['user_id'] = user_doc.id
            session['username'] = user_data['username']
            session['email'] = user_data['email']
            session['role'] = user_data['role']
            
            flash(f'Welcome back, {user_data["username"]}!', 'success')
            return redirect(url_for('dashboard'))
        else:
            flash('Invalid email or password.', 'error')
            return render_template('login.html')
    
    return render_template('login.html')

@app.route('/profile', methods=['GET', 'POST'])
def profile():
    if 'user_id' not in session:
        flash('Please log in to view your profile.')
        return redirect(url_for('login'))
    
    user_id = session['user_id']
    print(f"Loading profile for user_id: {user_id}")  # Debug line
    
    # Get user data from Firestore
    try:
        user_doc = db.collection('users').document(user_id).get()
        if not user_doc.exists:
            print(f"User document not found for user_id: {user_id}")  # Debug line
            flash('User profile not found.')
            return redirect(url_for('login'))
        
        user_data = user_doc.to_dict()
        user_data['id'] = user_doc.id
        print(f"User data loaded: {user_data.keys()}")  # Debug line
        
        # Handle POST request for profile updates
        if request.method == 'POST':
            # Update profile information
            updated_data = {}
            
            # Basic profile fields
            if request.form.get('username'):
                updated_data['username'] = request.form['username']
                session['username'] = request.form['username']  # Update session
            
            if request.form.get('full_name'):
                updated_data['full_name'] = request.form['full_name']
            
            if request.form.get('email'):
                updated_data['email'] = request.form['email']
                session['email'] = request.form['email']  # Update session
            
            if request.form.get('bio'):
                updated_data['bio'] = request.form['bio']
            
            if request.form.get('institution'):
                updated_data['institution'] = request.form['institution']
            
            # Handle avatar update
            avatar_type = request.form.get('avatar_type')
            avatar_id = request.form.get('avatar_id')
            if avatar_type and avatar_id:
                updated_data['avatar_type'] = avatar_type
                updated_data['avatar_id'] = avatar_id
            
            # Handle password change
            current_password = request.form.get('current_password')
            new_password = request.form.get('new_password')
            confirm_new_password = request.form.get('confirm_new_password')
            
            if current_password and new_password:
                if check_password_hash(user_data['password'], current_password):
                    if new_password == confirm_new_password:
                        if len(new_password) >= 6:
                            updated_data['password'] = generate_password_hash(new_password)
                            flash('Password updated successfully!', 'success')
                        else:
                            flash('Password must be at least 6 characters long.', 'error')
                            return redirect(url_for('profile'))
                    else:
                        flash('New passwords do not match.', 'error')
                        return redirect(url_for('profile'))
                else:
                    flash('Current password is incorrect.', 'error')
                    return redirect(url_for('profile'))
            
            # Update user document
            if updated_data:
                updated_data['updated_at'] = datetime.now()
                db.collection('users').document(user_id).update(updated_data)
                flash('Profile updated successfully!', 'success')
                return redirect(url_for('profile'))
        
        # Create a proper user object with default values for missing properties
        class UserProfile:
            def __init__(self, data):
                self.id = data.get('id')
                self.username = data.get('username', '')
                self.email = data.get('email', '')
                self.role = data.get('role', '')
                self.full_name = data.get('full_name', '')
                self.bio = data.get('bio', '')
                self.institution = data.get('institution', '')
                self.profile_picture = data.get('profile_picture', None)
                self.avatar_type = data.get('avatar_type', 'initial')
                self.avatar_id = data.get('avatar_id', 'blue')
                self.created_at = data.get('created_at', None)
                self.updated_at = data.get('updated_at', None)
        
        user = UserProfile(user_data)
        print(f"UserProfile created with avatar_type: {user.avatar_type}, avatar_id: {user.avatar_id}")  # Debug line
        
        # Get user statistics and recent activities
        stats = {}
        recent_activities = []
        
        try:
            if user_data['role'] == 'teacher':
                # Calculate teacher statistics
                subjects_query = db.collection('subjects').where('teacher_id', '==', user_id)
                subjects_docs = list(subjects_query.stream())
                subjects_count = len(subjects_docs)
                
                quizzes_query = db.collection('quizzes').where('teacher_id', '==', user_id)
                quizzes_docs = list(quizzes_query.stream())
                quizzes_count = len(quizzes_docs)
                
                # Calculate topics count
                topics_count = 0
                for subject_doc in subjects_docs:
                    subject_data = subject_doc.to_dict()
                    topics_count += subject_data.get('topic_count', 0)
                
                # Calculate quiz attempts and student engagement
                total_attempts = 0
                total_score = 0
                unique_students = set()
                
                for quiz_doc in quizzes_docs:
                    quiz_id = quiz_doc.id
                    attempts_query = db.collection('quiz_attempts').where('quiz_id', '==', quiz_id)
                    attempts_docs = list(attempts_query.stream())
                    
                    for attempt_doc in attempts_docs:
                        attempt_data = attempt_doc.to_dict()
                        total_attempts += 1
                        total_score += attempt_data.get('percentage', 0)
                        unique_students.add(attempt_data.get('user_id'))
                
                # Get enrolled students count from enrollments collection
                enrollments_query = db.collection('enrollments').where('teacher_id', '==', user_id).where('status', '==', 'active')
                enrolled_students_docs = list(enrollments_query.stream())
                total_students = len(set(doc.to_dict()['student_id'] for doc in enrolled_students_docs))
                
                avg_score = (total_score / total_attempts) if total_attempts > 0 else 0
                
                stats = {
                    'subjects_count': subjects_count,
                    'quizzes_count': quizzes_count,
                    'topics_count': topics_count,
                    'total_students': total_students,
                    'avg_completion_rate': 85,  # Placeholder - calculate based on your needs
                    'total_attempts': total_attempts,
                    'avg_score': round(avg_score, 1),
                    'active_students': total_students,
                    'monthly_views': total_attempts * 3,  # Rough estimate
                    'teaching_hours': topics_count * 2  # Rough estimate
                }
                
                # Get recent activities for teachers
                recent_activities = get_teacher_recent_activities(user_id)
                
            else:
                # Calculate student statistics - UPDATED to use enrollments
                attempts_query = db.collection('quiz_attempts').where('user_id', '==', user_id)
                attempts_docs = list(attempts_query.stream())
                
                quizzes_taken = len(attempts_docs)
                total_score = sum(attempt.to_dict().get('percentage', 0) for attempt in attempts_docs)
                average_score = (total_score / quizzes_taken) if quizzes_taken > 0 else 0
                
                # Get subjects enrolled from enrollments collection (FIXED)
                enrollments_query = db.collection('enrollments').where('student_id', '==', user_id).where('status', '==', 'active')
                enrollments_docs = list(enrollments_query.stream())
                subjects_enrolled = len(enrollments_docs)
                
                stats = {
                    'quizzes_taken': quizzes_taken,
                    'average_score': round(average_score, 1),
                    'subjects_enrolled': subjects_enrolled,  # Now correctly reflects actual enrollments
                    'study_hours': quizzes_taken * 0.5  # Rough estimate
                }
                
                # Get recent activities for students
                recent_activities = get_student_recent_activities(user_id)
                
        except Exception as e:
            print(f"Error calculating stats: {e}")
            import traceback
            traceback.print_exc()
            # Provide default empty stats
            if user_data['role'] == 'teacher':
                stats = {
                    'subjects_count': 0,
                    'quizzes_count': 0,
                    'topics_count': 0,
                    'total_students': 0,
                    'avg_completion_rate': 0,
                    'total_attempts': 0,
                    'avg_score': 0,
                    'active_students': 0,
                    'monthly_views': 0,
                    'teaching_hours': 0
                }
            else:
                stats = {
                    'quizzes_taken': 0,
                    'average_score': 0,
                    'subjects_enrolled': 0,
                    'study_hours': 0
                }
        
        return render_template('profile.html', 
                             user=user,
                             username=user.username,
                             role=user.role,
                             stats=stats,
                             recent_activities=recent_activities)
    
    except Exception as e:
        print(f"Error loading profile: {e}")
        import traceback
        traceback.print_exc()  # Print full error traceback
        flash('Error loading profile.')
        return redirect(url_for('dashboard'))

# Helper functions to get recent activities
def get_teacher_recent_activities(teacher_id, limit=5):
    """Get recent activities for teachers"""
    activities = []
    try:
        # Get recent enrollments
        recent_enrollments = db.collection('enrollments')\
            .where('teacher_id', '==', teacher_id)\
            .order_by('enrolled_at', direction=firestore.Query.DESCENDING)\
            .limit(limit).stream()
        
        for enrollment in recent_enrollments:
            data = enrollment.to_dict()
            activities.append({
                'description': f"New student {data['student_name']} enrolled in {data['subject_name']}",
                'created_at': data['enrolled_at']
            })
        
        # Get recent quiz attempts on teacher's quizzes
        teacher_quizzes = db.collection('quizzes').where('teacher_id', '==', teacher_id).stream()
        quiz_ids = [quiz.id for quiz in teacher_quizzes]
        
        if quiz_ids:
            for quiz_id in quiz_ids[:3]:  # Limit to prevent too many queries
                recent_attempts = db.collection('quiz_attempts')\
                    .where('quiz_id', '==', quiz_id)\
                    .order_by('created_at', direction=firestore.Query.DESCENDING)\
                    .limit(2).stream()
                
                for attempt in recent_attempts:
                    data = attempt.to_dict()
                    activities.append({
                        'description': f"Student completed quiz with {data.get('percentage', 0)}% score",
                        'created_at': data.get('created_at', datetime.now())
                    })
        
        # Sort activities by date and limit
        activities.sort(key=lambda x: x['created_at'], reverse=True)
        return activities[:limit]
        
    except Exception as e:
        print(f"Error getting teacher activities: {e}")
        return []

def get_student_recent_activities(student_id, limit=5):
    """Get recent activities for students"""
    activities = []
    try:
        # Get recent enrollments
        recent_enrollments = db.collection('enrollments')\
            .where('student_id', '==', student_id)\
            .order_by('enrolled_at', direction=firestore.Query.DESCENDING)\
            .limit(limit).stream()
        
        for enrollment in recent_enrollments:
            data = enrollment.to_dict()
            activities.append({
                'description': f"Enrolled in {data['subject_name']} by {data['teacher_name']}",
                'created_at': data['enrolled_at']
            })
        
        # Get recent quiz attempts
        recent_attempts = db.collection('quiz_attempts')\
            .where('user_id', '==', student_id)\
            .order_by('created_at', direction=firestore.Query.DESCENDING)\
            .limit(limit).stream()
        
        for attempt in recent_attempts:
            data = attempt.to_dict()
            activities.append({
                'description': f"Completed quiz with {data.get('percentage', 0)}% score",
                'created_at': data.get('created_at', datetime.now())
            })
        
        # Sort activities by date and limit
        activities.sort(key=lambda x: x['created_at'], reverse=True)
        return activities[:limit]
        
    except Exception as e:
        print(f"Error getting student activities: {e}")
        return []

# @app.route('/dashboard')
# def dashboard():
#     if 'user_id' not in session:
#         flash('Please log in to access your dashboard.')
#         return redirect(url_for('login'))
    
#     user_role = session.get('role')
#     username = session.get('username')
    
#     if user_role == 'teacher':
#         # Get teacher's subjects and quizzes (existing code remains the same)
#         subjects = []
#         quizzes = []
#         try:
#             subjects_ref = db.collection('subjects').where('teacher_id', '==', session['user_id'])
#             for doc in subjects_ref.stream():
#                 subject_data = doc.to_dict()
#                 subject_data['id'] = doc.id
                
#                 # Calculate topic count for each subject
#                 topics_ref = db.collection('topics').where('subject_id', '==', doc.id)
#                 topic_count = len(list(topics_ref.stream()))
#                 subject_data['topic_count'] = topic_count
                
#                 subjects.append(subject_data)
                
#             quizzes_ref = db.collection('quizzes').where('teacher_id', '==', session['user_id'])
#             for doc in quizzes_ref.stream():
#                 quiz_data = doc.to_dict()
#                 quiz_data['id'] = doc.id
#                 quizzes.append(quiz_data)
#         except Exception as e:
#             print(f"Error fetching teacher data: {e}")
        
#         return render_template('dashboard.html', role='teacher', username=username, subjects=subjects, quizzes=quizzes)
    
#     elif user_role == 'student':
#         # Get student's enrolled subjects and available subjects
#         enrolled_subjects = []
#         available_subjects = []
#         enrolled_quizzes = []
        
#         try:
#             # Get enrolled subjects
#             enrollments_ref = db.collection('enrollments').where('student_id', '==', session['user_id'])
#             enrolled_subject_ids = []
            
#             for doc in enrollments_ref.stream():
#                 enrollment_data = doc.to_dict()
#                 subject_id = enrollment_data['subject_id']
#                 enrolled_subject_ids.append(subject_id)
                
#                 # Get subject details
#                 subject_ref = db.collection('subjects').document(subject_id)
#                 subject_doc = subject_ref.get()
                
#                 if subject_doc.exists:
#                     subject_data = subject_doc.to_dict()
#                     subject_data['id'] = subject_doc.id
                    
#                     # Calculate topic count
#                     topics_ref = db.collection('topics').where('subject_id', '==', subject_id)
#                     topic_count = len(list(topics_ref.stream()))
#                     subject_data['topic_count'] = topic_count
#                     subject_data['enrollment_date'] = enrollment_data['enrolled_at']
                    
#                     enrolled_subjects.append(subject_data)
            
#             # Get available subjects (not enrolled)
#             all_subjects_ref = db.collection('subjects')
#             for doc in all_subjects_ref.stream():
#                 if doc.id not in enrolled_subject_ids:
#                     subject_data = doc.to_dict()
#                     subject_data['id'] = doc.id
                    
#                     # Calculate topic count
#                     topics_ref = db.collection('topics').where('subject_id', '==', doc.id)
#                     topic_count = len(list(topics_ref.stream()))
#                     subject_data['topic_count'] = topic_count
                    
#                     available_subjects.append(subject_data)
            
#             # Get quizzes from enrolled subjects
#             for subject_id in enrolled_subject_ids:
#                 quizzes_ref = db.collection('quizzes').where('subject_id', '==', subject_id).where('is_published', '==', True)
#                 for doc in quizzes_ref.stream():
#                     quiz_data = doc.to_dict()
#                     quiz_data['id'] = doc.id
#                     enrolled_quizzes.append(quiz_data)
                    
#         except Exception as e:
#             print(f"Error fetching student data: {e}")
        
#         return render_template('dashboard.html', 
#                              role='student', 
#                              username=username, 
#                              enrolled_subjects=enrolled_subjects,
#                              available_subjects=available_subjects, 
#                              quizzes=enrolled_quizzes)
@app.route('/dashboard')
def dashboard():
    if 'user_id' not in session:
        flash('Please log in to access your dashboard.', 'error')
        return redirect(url_for('login'))
    
    user_role = session.get('role')
    username = session.get('username')
    
    if user_role == 'teacher':
        # Get teacher's subjects and quizzes (existing code remains the same)
        subjects = []
        quizzes = []
        try:
            subjects_ref = db.collection('subjects').where('teacher_id', '==', session['user_id'])
            for doc in subjects_ref.stream():
                subject_data = doc.to_dict()
                subject_data['id'] = doc.id
                
                # Calculate topic count for each subject
                topics_ref = db.collection('topics').where('subject_id', '==', doc.id)
                topic_count = len(list(topics_ref.stream()))
                subject_data['topic_count'] = topic_count
                
                subjects.append(subject_data)
                
            quizzes_ref = db.collection('quizzes').where('teacher_id', '==', session['user_id'])
            for doc in quizzes_ref.stream():
                quiz_data = doc.to_dict()
                quiz_data['id'] = doc.id
                
                # Add subject name to quiz if not present
                if 'subject_name' not in quiz_data and quiz_data.get('subject_id'):
                    subject_doc = db.collection('subjects').document(quiz_data['subject_id']).get()
                    if subject_doc.exists:
                        quiz_data['subject_name'] = subject_doc.to_dict().get('name', 'Unknown Subject')
                
                # Add question count if not present
                if 'question_count' not in quiz_data:
                    questions_count = len(list(db.collection('questions').where('quiz_id', '==', doc.id).stream()))
                    quiz_data['question_count'] = questions_count
                
                quizzes.append(quiz_data)
                
        except Exception as e:
            print(f"Error fetching teacher data: {e}")
        
        return render_template('dashboard.html', 
                             role='teacher', 
                             username=username, 
                             subjects=subjects, 
                             quizzes=quizzes)
    
    elif user_role == 'student':
        # Get student's enrolled subjects with progress tracking
        enrolled_subjects = []
        enrolled_quizzes = []
        subject_progress = {}
        quiz_progress_data = {}
        
        try:
            # Get ONLY ACTIVE enrolled subjects
            enrollments_ref = db.collection('enrollments').where('student_id', '==', session['user_id']).where('status', '==', 'active')
            enrolled_subject_ids = []
            
            for doc in enrollments_ref.stream():
                enrollment_data = doc.to_dict()
                subject_id = enrollment_data['subject_id']
                enrolled_subject_ids.append(subject_id)
                
                # Get subject details
                subject_ref = db.collection('subjects').document(subject_id)
                subject_doc = subject_ref.get()
                
                if subject_doc.exists:
                    subject_data = subject_doc.to_dict()
                    subject_data['id'] = subject_doc.id
                    
                    # Get teacher name from subject data
                    subject_data['teacher_name'] = subject_data.get('teacher_name', 'Unknown Teacher')
                    
                    # Calculate topic count
                    topics_ref = db.collection('topics').where('subject_id', '==', subject_id)
                    topic_count = len(list(topics_ref.stream()))
                    subject_data['topic_count'] = topic_count
                    subject_data['enrollment_date'] = enrollment_data.get('enrolled_at')
                    
                    enrolled_subjects.append(subject_data)
                    
                    # Calculate quiz progress for this subject
                    subject_quizzes = list(db.collection('quizzes')
                                         .where('subject_id', '==', subject_id)
                                         .where('is_published', '==', True)
                                         .stream())
                    
                    total_quizzes = len(subject_quizzes)
                    passed_quizzes = 0
                    
                    for quiz_doc in subject_quizzes:
                        quiz_id = quiz_doc.id
                        
                        # Get user's best attempt for this quiz
                        attempts = list(db.collection('quiz_attempts')
                                      .where('quiz_id', '==', quiz_id)
                                      .where('user_id', '==', session['user_id'])
                                      .stream())
                        
                        if attempts:
                            best_score = max(attempt.to_dict().get('percentage', 0) for attempt in attempts)
                            attempts_count = len(attempts)
                            is_passed = best_score >= 70  # Assuming 70% is passing
                            
                            if is_passed:
                                passed_quizzes += 1
                            
                            quiz_progress_data[quiz_id] = {
                                'best_score': best_score,
                                'attempts_count': attempts_count,
                                'is_passed': is_passed
                            }
                        else:
                            quiz_progress_data[quiz_id] = {
                                'best_score': 0,
                                'attempts_count': 0,
                                'is_passed': False
                            }
                    
                    subject_progress[subject_id] = {
                        'total_quizzes': total_quizzes,
                        'passed_quizzes': passed_quizzes
                    }
            
            # Get quizzes ONLY from enrolled subjects with progress data
            for subject_id in enrolled_subject_ids:
                quizzes_ref = db.collection('quizzes').where('subject_id', '==', subject_id).where('is_published', '==', True)
                for doc in quizzes_ref.stream():
                    quiz_data = doc.to_dict()
                    quiz_data['id'] = doc.id
                    
                    # Get subject name for the quiz
                    subject_ref = db.collection('subjects').document(subject_id)
                    subject_doc = subject_ref.get()
                    if subject_doc.exists:
                        quiz_data['subject_name'] = subject_doc.to_dict().get('name', 'Unknown Subject')
                    
                    # Get question count
                    questions_ref = db.collection('questions').where('quiz_id', '==', doc.id)
                    question_count = len(list(questions_ref.stream()))
                    quiz_data['question_count'] = question_count
                    
                    enrolled_quizzes.append(quiz_data)
                    
        except Exception as e:
            print(f"Error fetching student data: {e}")
            import traceback
            traceback.print_exc()
        
        # Return enrolled subjects with progress data
        return render_template('dashboard.html', 
                             role='student', 
                             username=username, 
                             enrolled_subjects=enrolled_subjects,
                             quizzes=enrolled_quizzes,
                             subject_progress=subject_progress,
                             quiz_progress_data=quiz_progress_data)
    
    # Default redirect if role is not recognized
    return redirect(url_for('login'))
    
# Subject Management Routes
@app.route('/create-subject', methods=['GET', 'POST'])
def create_subject():
    if 'user_id' not in session or session.get('role') != 'teacher':
        flash('Access denied. Teachers only.', 'error')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        name = request.form['name']
        description = request.form['description']
        
        subject_data = {
            'name': name,
            'description': description,
            'teacher_id': session['user_id'],
            'teacher_name': session['username'],
            'created_at': datetime.now(),
            'topic_count': 0
        }
        
        try:
            db.collection('subjects').add(subject_data)
            flash('Subject created successfully!', 'success')
            return redirect(url_for('dashboard'))
        except Exception as e:
            flash(f'Error creating subject: {e}', 'error')
    
    return render_template('create_subject.html', username=session.get('username'), role=session.get('role'))

# Helper function to get student's completed topics
def get_student_completed_topics(student_id, subject_id=None):
    """Get list of topic IDs that student has completed"""
    try:
        query = db.collection('topic_completions').where('student_id', '==', student_id)
        if subject_id:
            query = query.where('subject_id', '==', subject_id)
        
        completed_topics = []
        for doc in query.stream():
            completed_topics.append(doc.to_dict()['topic_id'])
        return completed_topics
    except Exception as e:
        print(f"Error getting completed topics: {e}")
        return []

# Helper function to calculate subject progress
def calculate_subject_progress(student_id, subject_id):
    """Calculate completion percentage for a subject"""
    try:
        # Get total topics in subject
        total_topics = db.collection('topics').where('subject_id', '==', subject_id).stream()
        total_count = len(list(total_topics))
        
        if total_count == 0:
            return 0
        
        # Get completed topics for this subject
        completed_topics = get_student_completed_topics(student_id, subject_id)
        completed_count = len(completed_topics)
        
        progress_percentage = (completed_count / total_count) * 100
        return round(progress_percentage, 1)
    except Exception as e:
        print(f"Error calculating progress: {e}")
        return 0

# Route to mark topic as completed
@app.route('/topic/<topic_id>/complete', methods=['POST'])
def mark_topic_complete(topic_id):
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    try:
        # Get topic details
        topic_ref = db.collection('topics').document(topic_id)
        topic_doc = topic_ref.get()
        
        if not topic_doc.exists:
            return jsonify({'success': False, 'message': 'Topic not found'}), 404
        
        topic_data = topic_doc.to_dict()
        subject_id = topic_data['subject_id']
        student_id = session['user_id']
        
        # Check if student is enrolled
        if not check_enrollment(student_id, subject_id):
            return jsonify({'success': False, 'message': 'Not enrolled in this subject'}), 403
        
        # Check if already completed
        existing_completion = db.collection('topic_completions').where('student_id', '==', student_id).where('topic_id', '==', topic_id).limit(1).stream()
        if len(list(existing_completion)) > 0:
            return jsonify({'success': False, 'message': 'Topic already marked as completed'})
        
        # Mark as completed
        completion_data = {
            'student_id': student_id,
            'topic_id': topic_id,
            'subject_id': subject_id,
            'completed_at': datetime.now(),
            'student_name': session.get('username', 'Unknown')
        }
        
        db.collection('topic_completions').add(completion_data)
        
        # Calculate new progress percentage
        progress = calculate_subject_progress(student_id, subject_id)
        
        return jsonify({
            'success': True, 
            'message': 'Topic marked as completed!',
            'progress': progress
        })
        
    except Exception as e:
        print(f"Error marking topic complete: {e}")
        return jsonify({'success': False, 'message': 'Error marking topic as completed'}), 500

# Route to unmark topic as completed
@app.route('/topic/<topic_id>/uncomplete', methods=['POST'])
def unmark_topic_complete(topic_id):
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    try:
        student_id = session['user_id']
        
        # Find and delete the completion record
        completions = db.collection('topic_completions').where('student_id', '==', student_id).where('topic_id', '==', topic_id).limit(1).stream()
        
        deleted = False
        subject_id = None
        for completion in completions:
            subject_id = completion.to_dict()['subject_id']
            completion.reference.delete()
            deleted = True
            break
        
        if not deleted:
            return jsonify({'success': False, 'message': 'Topic was not marked as completed'})
        
        # Calculate new progress percentage
        progress = calculate_subject_progress(student_id, subject_id) if subject_id else 0
        
        return jsonify({
            'success': True, 
            'message': 'Topic unmarked as completed',
            'progress': progress
        })
        
    except Exception as e:
        print(f"Error unmarking topic: {e}")
        return jsonify({'success': False, 'message': 'Error unmarking topic'}), 500

# @app.route('/subject/<subject_id>')
# def view_subject(subject_id):
#     if 'user_id' not in session:
#         flash('Please log in to view subjects.')
#         return redirect(url_for('login'))
    
#     try:
#         # Get subject details
#         subject_ref = db.collection('subjects').document(subject_id)
#         subject_doc = subject_ref.get()
        
#         if not subject_doc.exists:
#             flash('Subject not found.')
#             return redirect(url_for('dashboard'))
        
#         subject_data = subject_doc.to_dict()
#         subject_data['id'] = subject_doc.id
        
#         # Get topics for this subject
#         topics = []
#         topics_ref = db.collection('topics').where('subject_id', '==', subject_id).order_by('created_at')
#         for doc in topics_ref.stream():
#             topic_data = doc.to_dict()
#             topic_data['id'] = doc.id
#             topics.append(topic_data)
        
#         return render_template('subject_detail.html', subject=subject_data, topics=topics)
#     except Exception as e:
#         flash(f'Error loading subject: {e}')
#         return redirect(url_for('dashboard'))

@app.route('/subject/<subject_id>')
def view_subject(subject_id):
    if 'user_id' not in session:
        flash('Please log in to view subjects.', 'error')
        return redirect(url_for('login'))
    
    try:
        # Get subject details
        subject_ref = db.collection('subjects').document(subject_id)
        subject_doc = subject_ref.get()
        
        if not subject_doc.exists:
            flash('Subject not found.', 'error')
            return redirect(url_for('dashboard'))
        
        subject_data = subject_doc.to_dict()
        subject_data['id'] = subject_doc.id
        
        # Check enrollment for students
        is_enrolled = False
        progress = 0
        completed_topics = []
        
        if session.get('role') == 'student':
            is_enrolled = check_enrollment(session['user_id'], subject_id)
            if not is_enrolled:
                # Allow viewing subject info but not topics
                return render_template('subject_detail.html', 
                                     subject=subject_data, 
                                     topics=[], 
                                     is_enrolled=False, 
                                     enrollment_required=True,
                                     progress=0, username=session.get('username'), role=session.get('role'))
            else:
                # Calculate progress and get completed topics
                progress = calculate_subject_progress(session['user_id'], subject_id)
                completed_topics = get_student_completed_topics(session['user_id'], subject_id)
        
        # Teachers can always view their own subjects
        elif session.get('role') == 'teacher' and subject_data['teacher_id'] != session['user_id']:
            flash('Access denied.', 'error')
            return redirect(url_for('dashboard'))
        
        # Get topics for this subject (only if enrolled or teacher)
        topics = []
        if is_enrolled or session.get('role') == 'teacher':
            topics_ref = db.collection('topics').where('subject_id', '==', subject_id).order_by('created_at')
            for doc in topics_ref.stream():
                topic_data = doc.to_dict()
                topic_data['id'] = doc.id
                # Add completion status for students
                if session.get('role') == 'student':
                    topic_data['is_completed'] = doc.id in completed_topics
                topics.append(topic_data)
        
        return render_template('subject_detail.html', 
                             subject=subject_data, 
                             topics=topics, 
                             is_enrolled=is_enrolled,
                             progress=progress,
                             completed_topics=completed_topics,
                             username=session.get('username'),
                             role=session.get('role'),
                             user_id=session.get('user_id'))

    except Exception as e:
        flash(f'Error loading subject: {e}')
        return redirect(url_for('dashboard'))
    
@app.route('/topic/<topic_id>')
def view_topic(topic_id):
    if 'user_id' not in session:
        flash('Please log in to view topics.')
        return redirect(url_for('login'))
    
    try:
        topic_ref = db.collection('topics').document(topic_id)
        topic_doc = topic_ref.get()
        
        if not topic_doc.exists:
            flash('Topic not found.')
            return redirect(url_for('dashboard'))
        
        topic_data = topic_doc.to_dict()
        topic_data['id'] = topic_doc.id
        
        # ✅ ENSURE PDF DATA IS AVAILABLE
        if 'pdf_url' not in topic_data:
            topic_data['pdf_url'] = ''
        
        if 'pdf_filename' not in topic_data:
            topic_data['pdf_filename'] = ''
        
        # DEBUG: Print PDF info
        if topic_data.get('pdf_url'):
            print(f"\n📄 Topic View - PDF Info:")
            print(f"   Topic ID: {topic_id}")
            print(f"   PDF URL: {topic_data['pdf_url']}")
            print(f"   PDF Filename: {topic_data.get('pdf_filename', 'No filename')}")
        
        # Check enrollment for students
        is_completed = False
        if session.get('role') == 'student':
            if not check_enrollment(session['user_id'], topic_data['subject_id']):
                flash('You need to enroll in this subject to view topics.')
                return redirect(url_for('view_subject', subject_id=topic_data['subject_id']))
            
            completions = db.collection('topic_completions')\
                .where('student_id', '==', session['user_id'])\
                .where('topic_id', '==', topic_id)\
                .limit(1).stream()
            
            is_completed = len(list(completions)) > 0
            
        elif session.get('role') == 'teacher' and topic_data['teacher_id'] != session['user_id']:
            flash('Access denied.')
            return redirect(url_for('dashboard'))
        
        topic_data['is_completed'] = is_completed
        
        return render_template('topic_detail.html', 
                             topic=topic_data,
                             username=session.get('username'),
                             role=session.get('role'),
                             user_id=session.get('user_id'))
        
    except Exception as e:
        print(f"❌ Error in view_topic: {e}")
        traceback.print_exc()
        flash(f'Error loading topic: {e}')
        return redirect(url_for('dashboard'))

@app.route('/student/<student_id>/progress/<subject_id>')
def get_student_progress(student_id, subject_id):
    if 'user_id' not in session:
        return jsonify({'error': 'Not authenticated'}), 401
    
    # Students can only view their own progress, teachers can view any student's progress
    if session.get('role') == 'student' and session['user_id'] != student_id:
        return jsonify({'error': 'Access denied'}), 403
    
    try:
        progress = calculate_subject_progress(student_id, subject_id)
        completed_topics = get_student_completed_topics(student_id, subject_id)
        
        # Get total topics count
        total_topics = db.collection('topics').where('subject_id', '==', subject_id).stream()
        total_count = len(list(total_topics))
        
        return jsonify({
            'progress': progress,
            'completed_count': len(completed_topics),
            'total_count': total_count,
            'completed_topics': completed_topics
        })
        
    except Exception as e:
        print(f"Error getting student progress: {e}")
        return jsonify({'error': 'Error fetching progress'}), 500

@app.route('/subject/<subject_id>/create-topic', methods=['GET', 'POST'])
def create_topic(subject_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        flash('Access denied. Teachers only.')
        return redirect(url_for('dashboard'))
    
    # Check if user owns this subject
    try:
        subject_ref = db.collection('subjects').document(subject_id)
        subject_doc = subject_ref.get()
        
        if not subject_doc.exists or subject_doc.to_dict()['teacher_id'] != session['user_id']:
            flash('Subject not found or access denied.')
            return redirect(url_for('dashboard'))
    except Exception as e:
        flash('Error accessing subject.')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        title = request.form.get('title', '').strip()
        content_text = request.form.get('content_text', '').strip()
        video_link = request.form.get('video_link', '').strip()
        
        # Validate title
        if not title:
            flash('Topic title is required.')
            return redirect(request.url)
        
        # Handle PDF file upload
        pdf_url = ''
        pdf_filename = ''
        
        if 'pdf_file' in request.files:
            file = request.files['pdf_file']
            
            if file and file.filename:
                # Validate file
                if not allowed_file(file.filename):
                    flash('Invalid file type. Only PDF files are allowed.')
                    return redirect(request.url)
                
                # Check file size
                file.seek(0, os.SEEK_END)
                file_size = file.tell()
                file.seek(0)
                
                if file_size > MAX_FILE_SIZE:
                    flash('File size exceeds 10MB limit.')
                    return redirect(request.url)
                
                # Generate unique filename
                original_filename = secure_filename(file.filename)
                unique_filename = f"{uuid.uuid4().hex}_{original_filename}"
                
                # Save file
                file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
                file.save(file_path)
                
                # Store relative URL for database
                pdf_url = f"/static/uploads/pdfs/topics/{unique_filename}"
                pdf_filename = original_filename
                
                print(f"✅ PDF uploaded successfully: {pdf_filename}")
                print(f"📁 File path: {file_path}")
                print(f"🔗 URL: {pdf_url}")
        
        # Create topic data
        topic_data = {
            'subject_id': subject_id,
            'title': title,
            'content_text': content_text,
            'video_link': video_link,
            'pdf_url': pdf_url,
            'pdf_filename': pdf_filename,
            'created_at': datetime.now(),
            'teacher_id': session['user_id']
        }
        
        try:
            # Add to database
            topic_ref = db.collection('topics').add(topic_data)
            topic_id = topic_ref[1].id
            
            # Update topic count in subject
            subject_ref.update({'topic_count': Increment(1)})
            
            flash(f'Topic "{title}" created successfully!')
            print(f"✅ Topic created with ID: {topic_id}")
            
            return redirect(url_for('view_subject', subject_id=subject_id))
            
        except Exception as e:
            print(f"❌ Error creating topic: {e}")
            traceback.print_exc()
            
            # Clean up uploaded file if database operation fails
            if pdf_url and os.path.exists(file_path):
                os.remove(file_path)
                
            flash('Error creating topic. Please try again.')
            return redirect(request.url)
    
    return render_template('create_topic.html', 
                         subject_id=subject_id, 
                         username=session.get('username'), 
                         role=session.get('role'))

@app.route('/topic/<topic_id>/edit', methods=['GET', 'POST'])
def edit_topic(topic_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        flash('Access denied. Teachers only.')
        return redirect(url_for('dashboard'))
    
    try:
        # Verify ownership
        topic_ref = db.collection('topics').document(topic_id)
        topic_doc = topic_ref.get()
        
        if not topic_doc.exists or topic_doc.to_dict()['teacher_id'] != session['user_id']:
            flash('Topic not found or access denied.')
            return redirect(url_for('dashboard'))
        
        topic_data = topic_doc.to_dict()
        topic_data['id'] = topic_doc.id
        
        # Ensure PDF data is included
        if 'pdf_url' not in topic_data:
            topic_data['pdf_url'] = ''
        
        if 'pdf_filename' not in topic_data:
            topic_data['pdf_filename'] = ''
        
        if request.method == 'POST':
            print("\n" + "="*80)
            print("🔧 EDIT TOPIC - POST REQUEST")
            print("="*80)
            
            title = request.form.get('title', '').strip()
            content_text = request.form.get('content_text', '').strip()
            video_link = request.form.get('video_link', '').strip()
            
            # Validate title
            if not title:
                flash('Topic title is required.')
                return redirect(request.url)
            
            # Handle PDF file upload
            pdf_url = topic_data.get('pdf_url', '')
            pdf_filename = topic_data.get('pdf_filename', '')
            old_pdf_path = None
            
            print(f"📄 Initial PDF State:")
            print(f"   Current PDF URL: {pdf_url}")
            print(f"   Current PDF Filename: {pdf_filename}")
            
            # Check if user wants to remove PDF
            remove_pdf = request.form.get('remove_pdf') == 'true'
            
            if remove_pdf and pdf_url:
                print(f"🗑️ User requested PDF removal")
                try:
                    # Extract just the filename from the URL
                    if '/static/uploads/pdfs/topics/' in pdf_url:
                        filename = pdf_url.split('/static/uploads/pdfs/topics/')[-1]
                        old_pdf_path = os.path.join(UPLOAD_FOLDER, filename)
                        print(f"   Old PDF path for deletion: {old_pdf_path}")
                except Exception as e:
                    print(f"⚠️ Error parsing PDF path for removal: {e}")
                
                pdf_url = ''
                pdf_filename = ''
            
            # Handle new PDF upload
            if 'pdf_file' in request.files:
                file = request.files['pdf_file']
                
                print(f"\n📁 File Upload Check:")
                print(f"   File present: {file is not None}")
                print(f"   Has filename: {bool(file and file.filename)}")
                if file:
                    print(f"   Filename: {file.filename}")
                
                if file and file.filename:
                    # Validate file
                    if not allowed_file(file.filename):
                        flash('Invalid file type. Only PDF files are allowed.')
                        return redirect(request.url)
                    
                    # Check file size
                    file.seek(0, os.SEEK_END)
                    file_size = file.tell()
                    file.seek(0)
                    
                    print(f"   File size: {file_size} bytes ({file_size / 1024 / 1024:.2f} MB)")
                    
                    if file_size > MAX_FILE_SIZE:
                        flash('File size exceeds 10MB limit.')
                        return redirect(request.url)
                    
                    # If replacing existing PDF, mark old one for deletion
                    if pdf_url and not old_pdf_path:
                        try:
                            if '/static/uploads/pdfs/topics/' in pdf_url:
                                filename = pdf_url.split('/static/uploads/pdfs/topics/')[-1]
                                old_pdf_path = os.path.join(UPLOAD_FOLDER, filename)
                                print(f"   Marking old PDF for replacement: {old_pdf_path}")
                        except Exception as e:
                            print(f"⚠️ Error parsing old PDF path: {e}")
                    
                    # Generate unique filename
                    original_filename = secure_filename(file.filename)
                    unique_filename = f"{uuid.uuid4().hex}_{original_filename}"
                    
                    print(f"\n💾 Saving New PDF:")
                    print(f"   Original filename: {original_filename}")
                    print(f"   Unique filename: {unique_filename}")
                    print(f"   Upload folder: {UPLOAD_FOLDER}")
                    
                    # Save new file
                    file_path = os.path.join(UPLOAD_FOLDER, unique_filename)
                    
                    try:
                        # Ensure directory exists
                        os.makedirs(UPLOAD_FOLDER, exist_ok=True)
                        print(f"   Upload folder exists: {os.path.exists(UPLOAD_FOLDER)}")
                        
                        # Save file
                        file.save(file_path)
                        
                        # Verify file was saved
                        file_exists = os.path.exists(file_path)
                        file_size_saved = os.path.getsize(file_path) if file_exists else 0
                        
                        print(f"\n✅ File Save Verification:")
                        print(f"   Full path: {file_path}")
                        print(f"   File exists: {file_exists}")
                        print(f"   File size: {file_size_saved} bytes")
                        
                        if not file_exists:
                            raise Exception(f"File was not saved to {file_path}")
                        
                        # ✅ CRITICAL: Use EXACT SAME FORMAT as create_topic
                        pdf_url = f"/static/uploads/pdfs/topics/{unique_filename}"
                        pdf_filename = original_filename
                        
                        print(f"\n📝 PDF Data to Store in Database:")
                        print(f"   PDF URL: {pdf_url}")
                        print(f"   PDF Filename: {pdf_filename}")
                        
                    except Exception as save_error:
                        print(f"\n❌ ERROR SAVING PDF:")
                        print(f"   Error: {save_error}")
                        traceback.print_exc()
                        flash('Error saving PDF file. Please try again.')
                        return redirect(request.url)
            
            # Update topic data
            update_data = {
                'title': title,
                'content_text': content_text,
                'video_link': video_link,
                'pdf_url': pdf_url,
                'pdf_filename': pdf_filename,
                'updated_at': datetime.now()
            }
            
            print(f"\n💿 Updating Database:")
            print(f"   Topic ID: {topic_id}")
            print(f"   PDF URL: {pdf_url}")
            print(f"   PDF Filename: {pdf_filename}")
            
            try:
                # Update in database
                topic_ref.update(update_data)
                print(f"✅ Database updated successfully")
                
                # Delete old PDF file if it was replaced or removed
                if old_pdf_path:
                    try:
                        if os.path.exists(old_pdf_path):
                            os.remove(old_pdf_path)
                            print(f"🗑️ Old PDF deleted: {old_pdf_path}")
                        else:
                            print(f"⚠️ Old PDF not found for deletion: {old_pdf_path}")
                    except Exception as delete_error:
                        print(f"⚠️ Could not delete old PDF: {delete_error}")
                
                flash(f'Topic "{title}" updated successfully!')
                print(f"\n" + "="*80)
                print(f"✅ EDIT TOPIC COMPLETED SUCCESSFULLY")
                print(f"="*80 + "\n")
                
                return redirect(url_for('view_topic', topic_id=topic_id))
                
            except Exception as e:
                print(f"\n❌ DATABASE UPDATE ERROR:")
                print(f"   Error: {e}")
                traceback.print_exc()
                
                # Clean up uploaded file if database update fails
                if pdf_url and 'unique_filename' in locals():
                    cleanup_path = os.path.join(UPLOAD_FOLDER, unique_filename)
                    if os.path.exists(cleanup_path):
                        os.remove(cleanup_path)
                        print(f"🗑️ Cleaned up failed upload: {cleanup_path}")
                
                flash('Error updating topic. Please try again.')
                return redirect(request.url)

        return render_template('edit_topic.html', 
                             topic=topic_data, 
                             username=session.get('username'), 
                             role=session.get('role'))

    except Exception as e:
        print(f"\n❌ CRITICAL ERROR in edit_topic:")
        print(f"   Error: {e}")
        traceback.print_exc()
        flash(f'Error accessing topic: {e}')
        return redirect(url_for('dashboard'))

@app.route('/topic/<topic_id>/delete', methods=['POST'])
def delete_topic(topic_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    try:
        # Verify ownership
        topic_ref = db.collection('topics').document(topic_id)
        topic_doc = topic_ref.get()
        
        if not topic_doc.exists or topic_doc.to_dict()['teacher_id'] != session['user_id']:
            return jsonify({'success': False, 'message': 'Topic not found or access denied'}), 404
        
        topic_data = topic_doc.to_dict()
        subject_id = topic_data['subject_id']
        
        # Delete PDF file if exists
        if topic_data.get('pdf_url'):
            pdf_path = os.path.join(app.static_folder, topic_data['pdf_url'].lstrip('/static/'))
            if os.path.exists(pdf_path):
                os.remove(pdf_path)
                print(f"🗑️ PDF deleted: {pdf_path}")
        
        # Delete topic completions
        completions_ref = db.collection('topic_completions').where('topic_id', '==', topic_id)
        for completion in completions_ref.stream():
            completion.reference.delete()
        
        # Delete the topic
        topic_ref.delete()
        
        # Update topic count in subject
        subject_ref = db.collection('subjects').document(subject_id)
        subject_ref.update({'topic_count': Increment(-1)})
        
        print(f"✅ Topic deleted: {topic_id}")
        return jsonify({'success': True, 'message': 'Topic deleted successfully'})
        
    except Exception as e:
        print(f"❌ Error deleting topic: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': 'Error deleting topic'}), 500
# Quiz Management Routes
@app.route('/create-quiz', methods=['GET', 'POST'])
def create_quiz():
    if 'user_id' not in session or session.get('role') != 'teacher':
        flash('Access denied. Teachers only.')
        return redirect(url_for('dashboard'))
    
    # Get teacher's subjects for dropdown
    subjects = []
    try:
        subjects_ref = db.collection('subjects').where('teacher_id', '==', session['user_id'])
        for doc in subjects_ref.stream():
            subject_data = doc.to_dict()
            subject_data['id'] = doc.id
            subjects.append(subject_data)
    except Exception as e:
        print(f"Error fetching subjects: {e}")
    
    if request.method == 'POST':
        title = request.form['title']
        description = request.form.get('description', '')
        subject_id = request.form['subject_id']
        time_limit = int(request.form.get('time_limit', 30))
        
        # Get subject name
        subject_name = ''
        try:
            subject_doc = db.collection('subjects').document(subject_id).get()
            if subject_doc.exists:
                subject_name = subject_doc.to_dict()['name']
        except Exception as e:
            print(f"Error getting subject name: {e}")
        
        quiz_data = {
            'title': title,
            'description': description,
            'subject_id': subject_id,
            'subject_name': subject_name,
            'teacher_id': session['user_id'],
            'teacher_name': session['username'],
            'time_limit': time_limit,
            'created_at': datetime.now(),
            'question_count': 0,
            'is_published': False
        }
        
        try:
            doc_ref = db.collection('quizzes').add(quiz_data)
            quiz_id = doc_ref[1].id
            flash('Quiz created successfully! Now add questions.')
            return redirect(url_for('manage_quiz', quiz_id=quiz_id))
        except Exception as e:
            flash(f'Error creating quiz: {e}')
    
    return render_template('create_quiz.html', subjects=subjects, username=session.get('username'), role=session.get('role'))

@app.route('/quiz/<quiz_id>/manage')
def manage_quiz(quiz_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        flash('Access denied. Teachers only.')
        return redirect(url_for('dashboard'))
    
    try:
        # Get quiz details
        quiz_ref = db.collection('quizzes').document(quiz_id)
        quiz_doc = quiz_ref.get()
        
        if not quiz_doc.exists or quiz_doc.to_dict()['teacher_id'] != session['user_id']:
            flash('Quiz not found or access denied.')
            return redirect(url_for('dashboard'))
        
        quiz_data = quiz_doc.to_dict()
        quiz_data['id'] = quiz_doc.id
        
        # Get questions for this quiz
        questions = []
        questions_ref = db.collection('questions').where('quiz_id', '==', quiz_id).order_by('created_at')
        for doc in questions_ref.stream():
            question_data = doc.to_dict()
            question_data['id'] = doc.id
            questions.append(question_data)
        
        return render_template('manage_quiz.html', quiz=quiz_data, questions=questions, username=session.get('username'), role=session.get('role'))
    except Exception as e:
        flash(f'Error loading quiz: {e}')
        return redirect(url_for('dashboard'))

@app.route('/quiz/<quiz_id>/publish')
def publish_quiz(quiz_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        flash('Access denied. Teachers only.')
        return redirect(url_for('dashboard'))
    
    try:
        quiz_ref = db.collection('quizzes').document(quiz_id)
        quiz_doc = quiz_ref.get()
        
        if not quiz_doc.exists or quiz_doc.to_dict()['teacher_id'] != session['user_id']:
            flash('Quiz not found or access denied.')
            return redirect(url_for('dashboard'))
        
        quiz_ref.update({'is_published': True})
        flash('Quiz published successfully!')
        # NEW: notifications
        quiz_data = quiz_doc.to_dict()
        subject_id = quiz_data.get('subject_id')
        title = quiz_data.get('title', 'A quiz')
        subject_name = quiz_data.get('subject_name', 'Subject')
        try:
            # notify teacher
            create_notification(
                user_id=session['user_id'],
                title='Quiz published',
                message=f"Published '{title}' in {subject_name}",
                notif_type='quiz',
                link_url=url_for('manage_quiz', quiz_id=quiz_id),
                icon='check-circle',
                metadata={'quiz_id': quiz_id, 'subject_id': subject_id}
            )
            # notify enrolled students
            if subject_id:
                enr = (db.collection('enrollments')
                         .where('subject_id', '==', subject_id)
                         .where('status', '==', 'active'))
                for d in enr.stream():
                    s = d.to_dict()
                    create_notification(
                        user_id=s['student_id'],
                        title='New quiz available',
                        message=f"'{title}' is now available in {subject_name}",
                        notif_type='quiz',
                        link_url=url_for('take_quiz', quiz_id=quiz_id),
                        icon='bell',
                        metadata={'quiz_id': quiz_id, 'subject_id': subject_id},
                        actor_id=session['user_id'],
                        actor_name=session.get('username')
                    )
        except Exception as ne:
            print(f"Notify error on publish: {ne}")
    except Exception as e:
        flash(f'Error publishing quiz: {e}')    
    return redirect(url_for('manage_quiz', quiz_id=quiz_id))


@app.route('/quiz/<quiz_id>/add-question', methods=['GET', 'POST'])
def add_question(quiz_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        flash('Access denied. Teachers only.')
        return redirect(url_for('dashboard'))
    
    # Verify quiz ownership
    try:
        quiz_ref = db.collection('quizzes').document(quiz_id)
        quiz_doc = quiz_ref.get()
        
        if not quiz_doc.exists or quiz_doc.to_dict()['teacher_id'] != session['user_id']:
            flash('Quiz not found or access denied.')
            return redirect(url_for('dashboard'))
        
        quiz_data = quiz_doc.to_dict()
    except Exception as e:
        flash('Error accessing quiz.')
        return redirect(url_for('dashboard'))
    
    if request.method == 'POST':
        # Handle JSON requests (CSV/Bulk import)
        if request.content_type == 'application/json' or request.form.get('method') in ['csv', 'bulk']:
            try:
                # Get data from JSON or form
                if request.is_json:
                    data = request.get_json()
                    method = data.get('method')
                    questions = data.get('questions', [])
                else:
                    method = request.form.get('method')
                    questions_json = request.form.get('questions')
                    questions = json.loads(questions_json) if questions_json else []
                
                if method in ['csv', 'bulk']:
                    added_count = 0
                    
                    for question in questions:
                        question_data = {
                            'quiz_id': quiz_id,
                            'question_type': question.get('type'),
                            'question_text': question.get('question') or question.get('text'),
                            'points': question.get('points', 1),
                            'created_at': datetime.now()
                        }
                        
                        # Handle different question types
                        q_type = question.get('type')
                        
                        if q_type == 'multiple_choice':
                            # Get choices from choicesArray or parse from choices string
                            choices = question.get('choicesArray', [])
                            if not choices and question.get('choices'):
                                choices = [choice.strip() for choice in question.get('choices').split('|')]
                            
                            if len(choices) >= 2:
                                # Pad with empty options if less than 4
                                while len(choices) < 4:
                                    choices.append('')
                                question_data['options'] = choices[:4]
                                
                                # Get correct answer
                                correct_answer = question.get('correct_answer', 'A')
                                # Convert index to letter if numeric
                                if correct_answer.isdigit():
                                    correct_index = int(correct_answer)
                                    if 0 <= correct_index < 4:
                                        correct_answer = chr(65 + correct_index)  # Convert 0->A, 1->B, etc.
                                    else:
                                        correct_answer = 'A'  # Default fallback
                                question_data['correct_answer'] = correct_answer
                            else:
                                # For CSV with old format compatibility
                                choices_from_bulk = question.get('choicesArray', [])
                                if choices_from_bulk and len(choices_from_bulk) >= 2:
                                    while len(choices_from_bulk) < 4:
                                        choices_from_bulk.append('')
                                    question_data['options'] = choices_from_bulk[:4]
                                    question_data['correct_answer'] = 'A'  # Default
                                else:
                                    continue
                        
                        elif q_type == 'true_false':
                            # Get correct answer from answers field
                            correct_answer = question.get('answers', question.get('correct_answer', 'true'))
                            
                            # Handle different types of correct_answer values
                            if isinstance(correct_answer, bool):
                                question_data['correct_answer'] = correct_answer
                            elif isinstance(correct_answer, str):
                                correct_answer_lower = correct_answer.lower()
                                if correct_answer_lower in ['true', '1', 'yes', 't']:
                                    question_data['correct_answer'] = True
                                else:
                                    question_data['correct_answer'] = False
                            elif isinstance(correct_answer, (int, float)):
                                question_data['correct_answer'] = bool(correct_answer)
                            else:
                                # Default fallback
                                question_data['correct_answer'] = True
                        
                        elif q_type in ['identification', 'enumeration']:
                            # Get answers from answersArray or parse from answers string
                            answers = question.get('answersArray', [])
                            if not answers and question.get('answers'):
                                answers_value = question.get('answers')
                                if isinstance(answers_value, str):
                                    answers = [answer.strip() for answer in answers_value.split('|')]
                                elif isinstance(answers_value, list):
                                    answers = [str(answer).strip() for answer in answers_value]
                            elif not answers and question.get('choices'):
                                # Fallback for bulk questions
                                choices_value = question.get('choices')
                                if isinstance(choices_value, str):
                                    answers = [answer.strip() for answer in choices_value.split('\n') if answer.strip()]
                                elif isinstance(choices_value, list):
                                    answers = [str(answer).strip() for answer in choices_value if str(answer).strip()]
                            
                            if answers:
                                question_data['correct_answers'] = answers
                            else:
                                # Skip questions without answers
                                continue
                        
                        # Add question to database
                        db.collection('questions').add(question_data)
                        added_count += 1
                    
                    # Update question count in quiz
                    quiz_ref.update({'question_count': Increment(added_count)})
                    
                    # Return JSON response
                    return jsonify({
                        'success': True, 
                        'message': f'Successfully imported {added_count} questions'
                    })
                    
            except Exception as e:
                return jsonify({
                    'success': False, 
                    'message': f'Error importing questions: {str(e)}'
                }), 400
        
        # Handle single question form submission (existing code)
        else:
            question_type = request.form['question_type']
            question_text = request.form['question_text']
            points = int(request.form.get('points', 1))
            
            question_data = {
                'quiz_id': quiz_id,
                'question_type': question_type,
                'question_text': question_text,
                'points': points,
                'created_at': datetime.now()
            }
            
            # Handle different question types
            if question_type == 'multiple_choice':
                options = [
                    request.form.get('option_a', ''),
                    request.form.get('option_b', ''),
                    request.form.get('option_c', ''),
                    request.form.get('option_d', '')
                ]
                correct_answer = request.form['correct_answer']
                question_data.update({
                    'options': options,
                    'correct_answer': correct_answer
                })
            
            elif question_type == 'true_false':
                correct_answer = request.form['tf_answer'] == 'true'
                question_data['correct_answer'] = correct_answer
            
            elif question_type in ['identification', 'enumeration']:
                correct_answers = [ans.strip() for ans in request.form['correct_answers'].split(',')]
                question_data['correct_answers'] = correct_answers
            
            try:
                db.collection('questions').add(question_data)
                
                # Update question count in quiz
                quiz_ref.update({'question_count': Increment(1)})
                
                flash('Question added successfully!')
                return redirect(url_for('manage_quiz', quiz_id=quiz_id))
            except Exception as e:
                flash(f'Error adding question: {e}')
    
    return render_template('add_question.html', quiz_id=quiz_id, quiz=quiz_data, username=session.get('username'), role=session.get('role'))

# Subject Edit and Delete Routes
@app.route('/subject/<subject_id>/edit', methods=['POST'])
def edit_subject(subject_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    try:
        # Verify ownership
        subject_ref = db.collection('subjects').document(subject_id)
        subject_doc = subject_ref.get()
        
        if not subject_doc.exists or subject_doc.to_dict()['teacher_id'] != session['user_id']:
            return jsonify({'success': False, 'message': 'Subject not found or access denied'}), 404
        
        # Get form data
        name = request.form.get('name', '').strip()
        description = request.form.get('description', '').strip()
        
        if not name:
            return jsonify({'success': False, 'message': 'Subject name is required'}), 400
        
        # Update subject
        update_data = {
            'name': name,
            'description': description,
            'updated_at': datetime.now()
        }
        
        subject_ref.update(update_data)
        
        return jsonify({'success': True, 'message': 'Subject updated successfully'})
        
    except Exception as e:
        print(f"Error updating subject: {e}")
        return jsonify({'success': False, 'message': 'Error updating subject'}), 500

@app.route('/subject/<subject_id>/delete', methods=['POST'])
def delete_subject(subject_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    try:
        # Verify ownership
        subject_ref = db.collection('subjects').document(subject_id)
        subject_doc = subject_ref.get()
        
        if not subject_doc.exists or subject_doc.to_dict()['teacher_id'] != session['user_id']:
            return jsonify({'success': False, 'message': 'Subject not found or access denied'}), 404
        
        # Delete all topics associated with this subject
        topics_ref = db.collection('topics').where('subject_id', '==', subject_id)
        topic_docs = topics_ref.stream()
        for topic_doc in topic_docs:
            topic_doc.reference.delete()
        
        # Delete all quizzes associated with this subject
        quizzes_ref = db.collection('quizzes').where('subject_id', '==', subject_id)
        quiz_docs = quizzes_ref.stream()
        for quiz_doc in quiz_docs:
            quiz_id = quiz_doc.id
            # Delete questions in each quiz
            questions_ref = db.collection('questions').where('quiz_id', '==', quiz_id)
            question_docs = questions_ref.stream()
            for question_doc in question_docs:
                question_doc.reference.delete()
            # Delete the quiz
            quiz_doc.reference.delete()
        
        # Finally delete the subject
        subject_ref.delete()
        
        return jsonify({'success': True, 'message': 'Subject deleted successfully'})
        
    except Exception as e:
        print(f"Error deleting subject: {e}")
        return jsonify({'success': False, 'message': 'Error deleting subject'}), 500

@app.route('/quiz/<quiz_id>/edit', methods=['GET', 'POST'])
def edit_quiz(quiz_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        flash('Access denied. Teachers only.')
        return redirect(url_for('dashboard'))
    
    try:
        # Verify ownership
        quiz_ref = db.collection('quizzes').document(quiz_id)
        quiz_doc = quiz_ref.get()
        
        if not quiz_doc.exists or quiz_doc.to_dict()['teacher_id'] != session['user_id']:
            flash('Quiz not found or access denied.')
            return redirect(url_for('dashboard'))
        
        quiz_data = quiz_doc.to_dict()
        quiz_data['id'] = quiz_doc.id
        
        if request.method == 'POST':
            # Handle quiz update
            title = request.form.get('title', '').strip()
            description = request.form.get('description', '').strip()
            time_limit = int(request.form.get('time_limit', 30))
            
            if not title:
                flash('Quiz title is required.')
                return render_template('edit_quiz.html', quiz=quiz_data)
            
            update_data = {
                'title': title,
                'description': description,
                'time_limit': time_limit,
                'updated_at': datetime.now()
            }
            
            quiz_ref.update(update_data)
            flash('Quiz updated successfully!')
            return redirect(url_for('manage_quiz', quiz_id=quiz_id), username=session.get('username'), role=session.get('role'))
        
        # Get teacher's subjects for dropdown
        subjects = []
        subjects_ref = db.collection('subjects').where('teacher_id', '==', session['user_id'])
        for doc in subjects_ref.stream():
            subject_data = doc.to_dict()
            subject_data['id'] = doc.id
            subjects.append(subject_data)
        
        return render_template('edit_quiz.html', quiz=quiz_data, subjects=subjects, username=session.get('username'), role=session.get('role'))
        
    except Exception as e:
        flash(f'Error accessing quiz: {e}')
        return redirect(url_for('dashboard'))

@app.route('/quiz/<quiz_id>/delete', methods=['POST'])
def delete_quiz(quiz_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    try:
        # Verify ownership
        quiz_ref = db.collection('quizzes').document(quiz_id)
        quiz_doc = quiz_ref.get()
        
        if not quiz_doc.exists or quiz_doc.to_dict()['teacher_id'] != session['user_id']:
            return jsonify({'success': False, 'message': 'Quiz not found or access denied'}), 404
        
        # Delete all questions associated with this quiz
        questions_ref = db.collection('questions').where('quiz_id', '==', quiz_id)
        question_docs = questions_ref.stream()
        for question_doc in question_docs:
            question_doc.reference.delete()
        
        # Delete the quiz
        quiz_ref.delete()
        
        return jsonify({'success': True, 'message': 'Quiz deleted successfully'})
        
    except Exception as e:
        print(f"Error deleting quiz: {e}")
        return jsonify({'success': False, 'message': 'Error deleting quiz'}), 500

@app.route('/quiz/<quiz_id>/preview')
def preview_quiz(quiz_id):
    if 'user_id' not in session:
        flash('Please log in to preview quizzes.')
        return redirect(url_for('login'))
    
    try:
        # Get quiz details
        quiz_ref = db.collection('quizzes').document(quiz_id)
        quiz_doc = quiz_ref.get()
        
        if not quiz_doc.exists:
            flash('Quiz not found.')
            return redirect(url_for('dashboard'))
        
        quiz_data = quiz_doc.to_dict()
        quiz_data['id'] = quiz_doc.id
        
        # For teachers, allow preview of their own quizzes regardless of publish status
        # For students, only allow preview of published quizzes
        if session.get('role') == 'student' and not quiz_data.get('is_published', False):
            flash('This quiz is not available.')
            return redirect(url_for('dashboard'))
        
        # Get questions for this quiz
        questions = []
        questions_ref = db.collection('questions').where('quiz_id', '==', quiz_id).order_by('created_at')
        for doc in questions_ref.stream():
            question_data = doc.to_dict()
            question_data['id'] = doc.id
            questions.append(question_data)
        
        return render_template('quiz_preview.html', quiz=quiz_data, questions=questions)
        
    except Exception as e:
        flash(f'Error loading quiz preview: {e}')
        return redirect(url_for('dashboard'))
    
@app.route('/quiz/<quiz_id>/question/<question_id>/edit', methods=['GET', 'POST'])
def edit_question(quiz_id, question_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        flash('Access denied. Teachers only.')
        return redirect(url_for('dashboard'))
    
    try:
        # Verify quiz ownership
        quiz_ref = db.collection('quizzes').document(quiz_id)
        quiz_doc = quiz_ref.get()
        
        if not quiz_doc.exists or quiz_doc.to_dict()['teacher_id'] != session['user_id']:
            flash('Quiz not found or access denied.')
            return redirect(url_for('dashboard'))
        
        quiz_data = quiz_doc.to_dict()
        quiz_data['id'] = quiz_doc.id
        
        # Get question data
        question_ref = db.collection('questions').document(question_id)
        question_doc = question_ref.get()
        
        if not question_doc.exists:
            flash('Question not found.')
            return redirect(url_for('manage_quiz', quiz_id=quiz_id))
        
        question_data = question_doc.to_dict()
        question_data['id'] = question_doc.id
        
        # Verify question belongs to this quiz
        if question_data['quiz_id'] != quiz_id:
            flash('Question does not belong to this quiz.')
            return redirect(url_for('manage_quiz', quiz_id=quiz_id))
        
        if request.method == 'POST':
            question_type = request.form['question_type']
            question_text = request.form['question_text']
            points = int(request.form.get('points', 1))
            
            update_data = {
                'question_type': question_type,
                'question_text': question_text,
                'points': points,
                'updated_at': datetime.now()
            }
            
            # Handle different question types
            if question_type == 'multiple_choice':
                options = [
                    request.form.get('option_a', ''),
                    request.form.get('option_b', ''),
                    request.form.get('option_c', ''),
                    request.form.get('option_d', '')
                ]
                correct_answer = request.form['correct_answer']
                update_data.update({
                    'options': options,
                    'correct_answer': correct_answer
                })
            
            elif question_type == 'true_false':
                correct_answer = request.form['tf_answer'] == 'true'
                update_data['correct_answer'] = correct_answer
            
            elif question_type in ['identification', 'enumeration']:
                correct_answers = [ans.strip() for ans in request.form['correct_answers'].split(',')]
                update_data['correct_answers'] = correct_answers
            
            try:
                question_ref.update(update_data)
                flash('Question updated successfully!')
                return redirect(url_for('manage_quiz', quiz_id=quiz_id))
            except Exception as e:
                flash(f'Error updating question: {e}')
        
        return render_template('edit_question.html', quiz=quiz_data, question=question_data, quiz_id=quiz_id, username=session.get('username'), role=session.get('role'))
        
    except Exception as e:
        flash(f'Error accessing question: {e}')
        return redirect(url_for('manage_quiz', quiz_id=quiz_id))

@app.route('/quiz/<quiz_id>/question/<question_id>/delete', methods=['POST'])
def delete_question(quiz_id, question_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    try:
        # Verify quiz ownership
        quiz_ref = db.collection('quizzes').document(quiz_id)
        quiz_doc = quiz_ref.get()
        
        if not quiz_doc.exists or quiz_doc.to_dict()['teacher_id'] != session['user_id']:
            return jsonify({'success': False, 'message': 'Quiz not found or access denied'}), 404
        
        # Get and verify question
        question_ref = db.collection('questions').document(question_id)
        question_doc = question_ref.get()
        
        if not question_doc.exists:
            return jsonify({'success': False, 'message': 'Question not found'}), 404
        
        question_data = question_doc.to_dict()
        
        # Verify question belongs to this quiz
        if question_data['quiz_id'] != quiz_id:
            return jsonify({'success': False, 'message': 'Question does not belong to this quiz'}), 400
        
        # Delete the question
        question_ref.delete()
        
        # Update question count in quiz
        quiz_ref.update({'question_count': Increment(-1)})
        
        return jsonify({'success': True, 'message': 'Question deleted successfully'})
        
    except Exception as e:
        print(f"Error deleting question: {e}")
        return jsonify({'success': False, 'message': 'Error deleting question'}), 500
   
@app.route('/quiz/<quiz_id>/take')
def take_quiz(quiz_id):
    if 'user_id' not in session:
        flash('Please log in to take quizzes.')
        return redirect(url_for('login'))
    
    try:
        # Get quiz details
        quiz_ref = db.collection('quizzes').document(quiz_id)
        quiz_doc = quiz_ref.get()
        
        if not quiz_doc.exists:
            flash('Quiz not found.')
            return redirect(url_for('dashboard'))
        
        quiz_data = quiz_doc.to_dict()
        quiz_data['id'] = quiz_doc.id
        
        # Check if quiz is published
        if not quiz_data.get('is_published', False):
            flash('This quiz is not available.')
            return redirect(url_for('dashboard'))
        
        # Check enrollment for students
        if session.get('role') == 'student':
            is_enrolled = check_enrollment(session['user_id'], quiz_data['subject_id'])
            if not is_enrolled:
                flash('You must be enrolled in this subject to take quizzes.')
                return redirect(url_for('view_subject', subject_id=quiz_data['subject_id']))
        
        # Get questions for this quiz
        questions = []
        questions_ref = db.collection('questions').where('quiz_id', '==', quiz_id).order_by('created_at')
        for doc in questions_ref.stream():
            question_data = doc.to_dict()
            question_data['id'] = doc.id
            questions.append(question_data)
        
        if not questions:
            flash('This quiz has no questions yet.')
            return redirect(url_for('dashboard'))
        
        # Check if user has already taken this quiz (if attempts are limited)
        if quiz_data.get('max_attempts', 0) > 0:
            attempts_count = db.collection('quiz_attempts').where('quiz_id', '==', quiz_id).where('user_id', '==', session['user_id']).stream()
            current_attempts = len(list(attempts_count))
            if current_attempts >= quiz_data['max_attempts']:
                flash(f'You have already used all {quiz_data["max_attempts"]} attempts for this quiz.')
                return redirect(url_for('quiz_results', quiz_id=quiz_id))
        
        # Add subject name if not present
        if 'subject_name' not in quiz_data and quiz_data.get('subject_id'):
            subject_doc = db.collection('subjects').document(quiz_data['subject_id']).get()
            if subject_doc.exists:
                quiz_data['subject_name'] = subject_doc.to_dict().get('name', 'Unknown Subject')
        
        return render_template('take_quiz.html', 
                             quiz=quiz_data, 
                             questions=questions,
                             username=session.get('username'),
                             role=session.get('role'))

    except Exception as e:
        flash(f'Error loading quiz: {e}')
        return redirect(url_for('dashboard'))


@app.route('/quiz/<quiz_id>/submit', methods=['POST'])
def submit_quiz(quiz_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please log in'}), 401
    
    try:
        # Get quiz details
        quiz_ref = db.collection('quizzes').document(quiz_id)
        quiz_doc = quiz_ref.get()
        
        if not quiz_doc.exists:
            return jsonify({'success': False, 'message': 'Quiz not found'}), 404
        
        quiz_data = quiz_doc.to_dict()
        
        # Get submitted answers
        submitted_answers = request.json.get('answers', {})
        
        # Get questions and calculate score
        questions_ref = db.collection('questions').where('quiz_id', '==', quiz_id)
        questions = list(questions_ref.stream())
        
        total_points = 0
        earned_points = 0
        results = {}
        
        for question_doc in questions:
            question_data = question_doc.to_dict()
            question_id = question_doc.id
            total_points += question_data.get('points', 1)
            
            user_answer = submitted_answers.get(question_id)
            is_correct = False
            
            # Check answer based on question type
            if question_data['question_type'] == 'multiple_choice':
                is_correct = user_answer == question_data.get('correct_answer')
            
            elif question_data['question_type'] == 'true_false':
                user_bool = user_answer == 'true' if isinstance(user_answer, str) else user_answer
                is_correct = user_bool == question_data.get('correct_answer')
            
            elif question_data['question_type'] in ['identification', 'enumeration']:
                correct_answers = question_data.get('correct_answers', [])
                if isinstance(user_answer, str):
                    # For identification, check if user answer matches any correct answer (case-insensitive)
                    user_answer_clean = user_answer.strip().lower()
                    is_correct = any(ans.strip().lower() == user_answer_clean for ans in correct_answers)
                elif isinstance(user_answer, list):
                    # For enumeration, check if all user answers are in correct answers
                    user_answers_clean = [ans.strip().lower() for ans in user_answer]
                    correct_answers_clean = [ans.strip().lower() for ans in correct_answers]
                    is_correct = all(ans in correct_answers_clean for ans in user_answers_clean)
            
            if is_correct:
                earned_points += question_data.get('points', 1)
            
            results[question_id] = {
                'user_answer': user_answer,
                'correct_answer': question_data.get('correct_answer') or question_data.get('correct_answers'),
                'is_correct': is_correct,
                'points': question_data.get('points', 1)
            }
        
        # Calculate percentage
        percentage = (earned_points / total_points * 100) if total_points > 0 else 0
        
        # Save quiz attempt to database
        attempt_data = {
            'quiz_id': quiz_id,
            'quiz_title': quiz_data.get('title'),
            'user_id': session['user_id'],
            'username': session['username'],
            'submitted_answers': submitted_answers,
            'results': results,
            'total_points': total_points,
            'earned_points': earned_points,
            'percentage': percentage,
            'submitted_at': datetime.now()
        }
        
        attempt_ref = db.collection('quiz_attempts').add(attempt_data)
        attempt_id = attempt_ref[1].id
        # NEW: notifications
        try:
            # student: result
            create_notification(
                user_id=session['user_id'],
                title='Quiz completed',
                message=f"You scored {round(percentage, 2)}% on '{quiz_data.get('title', 'Quiz')}'",
                notif_type='quiz',
                link_url=url_for('view_attempt', attempt_id=attempt_id),
                icon='check-circle',
                metadata={'quiz_id': quiz_id, 'attempt_id': attempt_id}
            )
            # teacher: student attempted quiz
            teacher_id = quiz_data.get('teacher_id')
            if teacher_id:
                create_notification(
                    user_id=teacher_id,
                    title='Quiz attempt',
                    message=f"{session['username']} scored {round(percentage, 2)}% on '{quiz_data.get('title', 'Quiz')}'",
                    notif_type='quiz',
                    link_url=url_for('manage_quiz', quiz_id=quiz_id),
                    icon='bell',
                    metadata={'quiz_id': quiz_id, 'attempt_id': attempt_id},
                    actor_id=session['user_id'],
                    actor_name=session['username']
                )
        except Exception as ne:
            print(f"Notify error on submit: {ne}")

        return jsonify({
            'success': True,
            'attempt_id': attempt_id,
            'score': earned_points,
            'total': total_points,
            'percentage': round(percentage, 2)
        })
        
        # return jsonify({
        #     'success': True,
        #     'attempt_id': attempt_id,
        #     'score': earned_points,
        #     'total': total_points,
        #     'percentage': round(percentage, 2)
        # })
        
    except Exception as e:
        print(f"Error submitting quiz: {e}")
        return jsonify({'success': False, 'message': 'Error submitting quiz'}), 500


# Add this debugging route to test if your Flask app is working
@app.route('/debug/routes')
def debug_routes():
    """Debug route to see all registered routes"""
    import urllib
    output = []
    for rule in app.url_map.iter_rules():
        methods = ','.join(rule.methods)
        line = urllib.parse.unquote("{:50s} {:20s} {}".format(rule.endpoint, methods, rule))
        output.append(line)
    
    return '<pre>' + '\n'.join(sorted(output)) + '</pre>'

# Make sure your quiz_results route is properly defined with error handling
@app.route('/quiz/<quiz_id>/results')
def quiz_results(quiz_id):
    print(f"DEBUG: Accessing quiz results for quiz_id: {quiz_id}")
    
    if 'user_id' not in session:
        flash('Please log in to view results.')
        return redirect(url_for('login'))
    
    try:
        # Get quiz details
        quiz_ref = db.collection('quizzes').document(quiz_id)
        quiz_doc = quiz_ref.get()
        
        print(f"DEBUG: Quiz document exists: {quiz_doc.exists}")
        
        if not quiz_doc.exists:
            flash('Quiz not found.')
            return redirect(url_for('dashboard'))
        
        quiz_data = quiz_doc.to_dict()
        quiz_data['id'] = quiz_doc.id
        
        print(f"DEBUG: Quiz data loaded: {quiz_data.get('title', 'No title')}")
        
        # For teachers, allow viewing results of their own quizzes
        # For students, only allow viewing results of published quizzes they're enrolled in
        if session.get('role') == 'student':
            if not quiz_data.get('is_published', False):
                flash('This quiz is not available.')
                return redirect(url_for('dashboard'))
            
            # Check enrollment for students
            is_enrolled = check_enrollment(session['user_id'], quiz_data['subject_id'])
            if not is_enrolled:
                flash('You must be enrolled in this subject to view quiz results.')
                return redirect(url_for('view_subject', subject_id=quiz_data['subject_id']))
        
        elif session.get('role') == 'teacher':
            # Teachers can only view results for their own quizzes
            if quiz_data.get('teacher_id') != session['user_id']:
                flash('You can only view results for your own quizzes.')
                return redirect(url_for('dashboard'))
        
        # Get user's attempts for this quiz
        attempts = []
        attempts_ref = db.collection('quiz_attempts').where('quiz_id', '==', quiz_id).where('user_id', '==', session['user_id']).order_by('submitted_at', direction=firestore.Query.DESCENDING)
        
        for doc in attempts_ref.stream():
            attempt_data = doc.to_dict()
            attempt_data['id'] = doc.id
            attempts.append(attempt_data)
        
        print(f"DEBUG: Found {len(attempts)} attempts")
        
        # Add question count to quiz data if not present
        if 'question_count' not in quiz_data:
            questions_count = len(list(db.collection('questions').where('quiz_id', '==', quiz_id).stream()))
            quiz_data['question_count'] = questions_count
        
        # Get subject and teacher names for display
        if 'subject_name' not in quiz_data and quiz_data.get('subject_id'):
            subject_doc = db.collection('subjects').document(quiz_data['subject_id']).get()
            if subject_doc.exists:
                quiz_data['subject_name'] = subject_doc.to_dict().get('name', 'Unknown Subject')
        
        if 'teacher_name' not in quiz_data and quiz_data.get('teacher_id'):
            teacher_doc = db.collection('users').document(quiz_data['teacher_id']).get()
            if teacher_doc.exists:
                teacher_data = teacher_doc.to_dict()
                quiz_data['teacher_name'] = f"{teacher_data.get('first_name', '')} {teacher_data.get('last_name', '')}".strip()
        
        return render_template('quiz_results.html', 
                             quiz=quiz_data, 
                             attempts=attempts,
                             username=session.get('username'),
                             role=session.get('role'))
        
    except Exception as e:
        print(f"ERROR in quiz_results: {e}")
        flash(f'Error loading results: {e}')
        return redirect(url_for('dashboard'))


@app.route('/quiz-attempt/<attempt_id>')
def view_attempt(attempt_id):
    if 'user_id' not in session:
        flash('Please log in to view attempt details.')
        return redirect(url_for('login'))
    
    try:
        # Get attempt details
        attempt_ref = db.collection('quiz_attempts').document(attempt_id)
        attempt_doc = attempt_ref.get()
        
        if not attempt_doc.exists:
            flash('Attempt not found.')
            return redirect(url_for('dashboard'))
        
        attempt_data = attempt_doc.to_dict()
        attempt_data['id'] = attempt_doc.id
        
        # Verify user owns this attempt
        if attempt_data['user_id'] != session['user_id']:
            flash('Access denied.')
            return redirect(url_for('dashboard'))
        
        # Get quiz details
        quiz_data = None
        try:
            quiz_ref = db.collection('quizzes').document(attempt_data['quiz_id'])
            quiz_doc = quiz_ref.get()
            if quiz_doc.exists:
                quiz_data = quiz_doc.to_dict()
                quiz_data['id'] = quiz_doc.id
                
                # Add subject name if available
                if 'subject_name' not in quiz_data and quiz_data.get('subject_id'):
                    subject_doc = db.collection('subjects').document(quiz_data['subject_id']).get()
                    if subject_doc.exists:
                        quiz_data['subject_name'] = subject_doc.to_dict().get('name', 'Unknown Subject')
                        
        except Exception as e:
            print(f"Error loading quiz data: {e}")
        
        # Get questions with details
        questions = []
        questions_ref = db.collection('questions').where('quiz_id', '==', attempt_data['quiz_id']).order_by('created_at')
        for doc in questions_ref.stream():
            question_data = doc.to_dict()
            question_data['id'] = doc.id
            questions.append(question_data)
        
        return render_template('attempt_detail.html', 
                             attempt=attempt_data, 
                             quiz=quiz_data,
                             questions=questions,
                             username=session.get('username'),
                             role=session.get('role'))

    except Exception as e:
        flash(f'Error loading attempt: {e}')
        return redirect(url_for('dashboard'))

@app.route('/subject/<subject_id>/enroll', methods=['POST'])
def enroll_subject(subject_id):
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({'success': False, 'message': 'Students only'}), 403
    
    try:
        # Check if subject exists
        subject_ref = db.collection('subjects').document(subject_id)
        subject_doc = subject_ref.get()
        
        if not subject_doc.exists:
            return jsonify({'success': False, 'message': 'Subject not found'}), 404
        
        subject_data = subject_doc.to_dict()
        
        # Check if already enrolled (ACTIVE enrollments only)
        enrollments_ref = db.collection('enrollments')
        existing_enrollment = enrollments_ref.where('student_id', '==', session['user_id']).where('subject_id', '==', subject_id).where('status', '==', 'active').get()
        
        if len(existing_enrollment) > 0:  # Check length instead of truthiness
            return jsonify({'success': False, 'message': 'Already enrolled in this subject'}), 400
        
        # Create enrollment
        enrollment_data = {
            'student_id': session['user_id'],
            'student_name': session['username'],
            'subject_id': subject_id,
            'subject_name': subject_data['name'],
            'teacher_id': subject_data['teacher_id'],
            'teacher_name': subject_data['teacher_name'],
            'enrolled_at': datetime.now(),
            'status': 'active'
        }
        
        enrollments_ref.add(enrollment_data)
 # NEW: notifications
        try:
            # notify teacher
            create_notification(
                user_id=subject_data['teacher_id'],
                title='New enrollment',
                message=f"{session['username']} enrolled in {subject_data['name']}",
                notif_type='enrollment',
                link_url=url_for('view_subject', subject_id=subject_id),
                icon='user-plus',
                metadata={'subject_id': subject_id, 'student_id': session['user_id']},
                actor_id=session['user_id'],
                actor_name=session['username']
            )
            # confirm to student
            create_notification(
                user_id=session['user_id'],
                title='Enrolled successfully',
                message=f"You enrolled in {subject_data['name']}",
                notif_type='enrollment',
                link_url=url_for('view_subject', subject_id=subject_id),
                icon='book-open',
                metadata={'subject_id': subject_id}
            )
        except Exception as ne:
            print(f"Notify error on enroll: {ne}")
        
        return jsonify({'success': True, 'message': 'Successfully enrolled in subject'})
        
    except Exception as e:
        print(f"Error enrolling in subject: {e}")
        return jsonify({'success': False, 'message': 'Error enrolling in subject'}), 500

@app.route('/subject/<subject_id>/unenroll', methods=['POST'])
def unenroll_subject(subject_id):
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({'success': False, 'message': 'Students only'}), 403
    
    try:
        # Find ACTIVE enrollment only
        enrollments_ref = db.collection('enrollments')
        enrollment_docs = enrollments_ref.where('student_id', '==', session['user_id']).where('subject_id', '==', subject_id).where('status', '==', 'active').get()
        
        if len(enrollment_docs) == 0:  # Check length instead of truthiness
            return jsonify({'success': False, 'message': 'Not enrolled in this subject'}), 400
        
        # Update enrollment status to 'inactive' instead of deleting (for activity tracking)
        for doc in enrollment_docs:
            doc.reference.update({
                'status': 'inactive',
                'unenrolled_at': datetime.now()
            })
        
        return jsonify({'success': True, 'message': 'Successfully unenrolled from subject'})
        
    except Exception as e:
        print(f"Error unenrolling from subject: {e}")
        return jsonify({'success': False, 'message': 'Error unenrolling from subject'}), 500

# Helper function to check enrollment (this one is correct)
def check_enrollment(student_id, subject_id):
    """Check if a student is enrolled in a subject"""
    try:
        enrollments_ref = db.collection('enrollments')
        enrollment_docs = enrollments_ref.where('student_id', '==', student_id).where('subject_id', '==', subject_id).where('status', '==', 'active').get()
        return len(enrollment_docs) > 0
    except:
        return False

@app.route('/logout')
def logout():
    session.clear()
    flash('You have been logged out successfully.', 'success')
    return redirect(url_for('home'))


# Optional: Cleanup expired reset tokens (run this periodically)
@app.route('/admin/cleanup-expired-tokens', methods=['POST'])
def cleanup_expired_tokens():
    """Admin route to clean up expired password reset tokens"""
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Access denied'}), 403
    
    try:
        # Delete expired tokens
        resets_ref = db.collection('password_resets')
        expired_docs = resets_ref.where('expires_at', '<', datetime.now()).get()
        
        deleted_count = 0
        for doc in expired_docs:
            doc.reference.delete()
            deleted_count += 1
        
        return jsonify({
            'success': True, 
            'message': f'Cleaned up {deleted_count} expired tokens'
        })
        
    except Exception as e:
        return jsonify({
            'success': False, 
            'message': f'Error cleaning up tokens: {e}'
        }), 500



@app.route('/forgot-password', methods=['GET', 'POST'])
def forgot_password():
    if request.method == 'POST':
        email = request.form.get('email', '').strip()
        
        if not email:
            flash('Please enter your email address.')
            return render_template('forgot_password.html')
        
        try:
            # Check if user exists
            users_ref = db.collection('users')
            user_docs = list(users_ref.where('email', '==', email).get())
            
            if not user_docs:
                # Don't reveal if email exists or not for security
                flash('If an account with that email exists, we\'ve sent you a password reset link.')
                return redirect(url_for('login'))
            
            user_doc = user_docs[0]
            user_data = user_doc.to_dict()
            user_id = user_doc.id
            
            # Generate reset token
            reset_token = secrets.token_urlsafe(32)
            token_hash = hashlib.sha256(reset_token.encode()).hexdigest()
            
            # Set token expiration (1 hour from now) - using UTC to avoid timezone issues
            from datetime import datetime, timedelta, timezone
            current_time = datetime.now(timezone.utc)
            expires_at = current_time + timedelta(hours=1)
            
            print(f"DEBUG: Creating reset token")
            print(f"DEBUG: Current time (UTC): {current_time}")
            print(f"DEBUG: Expires at (UTC): {expires_at}")
            print(f"DEBUG: Token: {reset_token}")
            print(f"DEBUG: Token hash: {token_hash}")
            
            # Store reset token in database
            reset_data = {
                'user_id': user_id,
                'token_hash': token_hash,
                'email': email,
                'expires_at': expires_at,
                'used': False,
                'created_at': current_time
            }
            
            reset_ref = db.collection('password_resets').add(reset_data)
            print(f"DEBUG: Reset token stored with ID: {reset_ref[1].id}")
            
            # Send reset email
            reset_url = url_for('reset_password', token=reset_token, _external=True)
            print(f"DEBUG: Reset URL: {reset_url}")
            
            msg = Message(
                'Reset Your Quizera Password',
                recipients=[email],
                html=f'''
                <html>
                <body>
                    <div style="font-family: Arial, sans-serif; max-width: 600px; margin: 0 auto;">
                        <h2 style="color: #2563eb;">Reset Your Password</h2>
                        <p>Hello {user_data.get('username', 'there')},</p>
                        <p>You requested to reset your password for your Quizera account. Click the button below to reset it:</p>
                        <div style="text-align: center; margin: 30px 0;">
                            <a href="{reset_url}" 
                               style="background-color: #2563eb; color: white; padding: 12px 24px; 
                                      text-decoration: none; border-radius: 5px; display: inline-block;">
                                Reset Password
                            </a>
                        </div>
                        <p>Or copy and paste this link into your browser:</p>
                        <p><a href="{reset_url}">{reset_url}</a></p>
                        <p><strong>This link will expire in 1 hour.</strong></p>
                        <p>If you didn't request this password reset, please ignore this email.</p>
                        <hr style="margin: 30px 0; border: 1px solid #e5e5e5;">
                        <p style="color: #666; font-size: 12px;">
                            This is an automated message from Quizera. Please do not reply to this email.
                        </p>
                    </div>
                </body>
                </html>
                '''
            )
            
            mail.send(msg)
            print("DEBUG: Email sent successfully")
            flash('If an account with that email exists, we\'ve sent you a password reset link.')
            return redirect(url_for('login'))
            
        except Exception as e:
            print(f"ERROR in forgot_password: {e}")
            import traceback
            traceback.print_exc()
            flash('An error occurred. Please try again later.')
    
    return render_template('forgot_password.html')

@app.route('/reset-password/<token>', methods=['GET', 'POST'])
def reset_password(token):
    print(f"DEBUG: Reset password accessed with token: {token}")
    print(f"DEBUG: Request method: {request.method}")
    
    if request.method == 'GET':
        # Verify token exists and is valid
        token_hash = hashlib.sha256(token.encode()).hexdigest()
        print(f"DEBUG: Token hash: {token_hash}")
        
        try:
            resets_ref = db.collection('password_resets')
            reset_docs = list(resets_ref.where('token_hash', '==', token_hash).where('used', '==', False).get())
            
            print(f"DEBUG: Found {len(reset_docs)} matching reset docs")
            
            if not reset_docs:
                print("DEBUG: No matching reset docs found")
                flash('Invalid or expired reset link.')
                return redirect(url_for('forgot_password'))
            
            reset_doc = reset_docs[0]
            reset_data = reset_doc.to_dict()
            
            print(f"DEBUG: Reset data: {reset_data}")
            
            # Get current time in UTC and ensure comparison consistency
            from datetime import datetime, timezone
            current_time = datetime.now(timezone.utc)
            print(f"DEBUG: Current time (UTC): {current_time}")
            
            # Handle the expires_at field - it might be stored differently
            expires_at = reset_data['expires_at']
            print(f"DEBUG: Expires at (raw): {expires_at}")
            print(f"DEBUG: Expires at type: {type(expires_at)}")
            
            # Convert expires_at to UTC datetime if needed
            if hasattr(expires_at, 'timestamp'):
                # If it's a Firestore timestamp, convert to datetime
                expires_at_dt = expires_at.replace(tzinfo=timezone.utc)
            elif isinstance(expires_at, datetime):
                # If it's already a datetime, ensure it has timezone info
                if expires_at.tzinfo is None:
                    expires_at_dt = expires_at.replace(tzinfo=timezone.utc)
                else:
                    expires_at_dt = expires_at.astimezone(timezone.utc)
            else:
                # Fallback - assume it's a naive datetime and add UTC timezone
                expires_at_dt = datetime.fromisoformat(str(expires_at)).replace(tzinfo=timezone.utc)
            
            print(f"DEBUG: Expires at (converted): {expires_at_dt}")
            
            # Check if token is expired
            if current_time > expires_at_dt:
                print("DEBUG: Token has expired")
                flash('This reset link has expired. Please request a new one.')
                return redirect(url_for('forgot_password'))
            
            print("DEBUG: Token is valid, rendering reset password page")
            return render_template('reset_password.html', token=token, email=reset_data.get('email'))
            
        except Exception as e:
            print(f"ERROR in reset_password GET: {e}")
            import traceback
            traceback.print_exc()
            flash('An error occurred. Please try again.')
            return redirect(url_for('forgot_password'))
    
    elif request.method == 'POST':
        print("DEBUG: Processing POST request for password reset")
        
        new_password = request.form.get('new_password', '').strip()
        confirm_password = request.form.get('confirm_password', '').strip()
        
        print(f"DEBUG: New password length: {len(new_password) if new_password else 0}")
        print(f"DEBUG: Passwords match: {new_password == confirm_password}")
        
        if not new_password or not confirm_password:
            flash('Please fill in all fields.')
            return render_template('reset_password.html', token=token)
        
        if len(new_password) < 6:
            flash('Password must be at least 6 characters long.')
            return render_template('reset_password.html', token=token)
        
        if new_password != confirm_password:
            flash('Passwords do not match.')
            return render_template('reset_password.html', token=token)
        
        try:
            token_hash = hashlib.sha256(token.encode()).hexdigest()
            
            # Find and validate reset token
            resets_ref = db.collection('password_resets')
            reset_docs = list(resets_ref.where('token_hash', '==', token_hash).where('used', '==', False).get())
            
            print(f"DEBUG: Found {len(reset_docs)} reset docs for POST")
            
            if not reset_docs:
                print("DEBUG: No valid reset token found for POST")
                flash('Invalid or expired reset link.')
                return redirect(url_for('forgot_password'))
            
            reset_doc = reset_docs[0]
            reset_data = reset_doc.to_dict()
            
            # Check if token is expired (same logic as GET)
            from datetime import datetime, timezone
            current_time = datetime.now(timezone.utc)
            expires_at = reset_data['expires_at']
            
            # Convert expires_at to UTC datetime if needed
            if hasattr(expires_at, 'timestamp'):
                expires_at_dt = expires_at.replace(tzinfo=timezone.utc)
            elif isinstance(expires_at, datetime):
                if expires_at.tzinfo is None:
                    expires_at_dt = expires_at.replace(tzinfo=timezone.utc)
                else:
                    expires_at_dt = expires_at.astimezone(timezone.utc)
            else:
                expires_at_dt = datetime.fromisoformat(str(expires_at)).replace(tzinfo=timezone.utc)
            
            if current_time > expires_at_dt:
                print("DEBUG: Token expired during POST")
                flash('This reset link has expired. Please request a new one.')
                return redirect(url_for('forgot_password'))
            
            print(f"DEBUG: Updating password for user: {reset_data['user_id']}")
            
            # Update user password
            user_ref = db.collection('users').document(reset_data['user_id'])
            hashed_password = generate_password_hash(new_password)
            
            user_ref.update({
                'password': hashed_password,
                'password_updated_at': current_time
            })
            
            print("DEBUG: Password updated successfully")
            
            # Mark reset token as used
            reset_doc.reference.update({
                'used': True,
                'used_at': current_time
            })
            
            print("DEBUG: Reset token marked as used")
            
            flash('Your password has been updated successfully! You can now log in.')
            return redirect(url_for('login'))
            
        except Exception as e:
            print(f"ERROR in reset_password POST: {e}")
            import traceback
            traceback.print_exc()
            flash('An error occurred while resetting your password. Please try again.')
            return render_template('reset_password.html', token=token)

@app.route('/browse-subjects')
def browse_subjects():
    if 'user_id' not in session or session.get('role') != 'student':
        flash('Students only access.')
        return redirect(url_for('login'))
    
    try:
        # Get already enrolled subject IDs
        enrollments_ref = db.collection('enrollments').where('student_id', '==', session['user_id']).where('status', '==', 'active')
        enrolled_subject_ids = []
        
        for doc in enrollments_ref.stream():
            enrolled_subject_ids.append(doc.to_dict()['subject_id'])
        
        # Get all subjects not enrolled in
        available_subjects = []
        all_subjects_ref = db.collection('subjects')
        
        for doc in all_subjects_ref.stream():
            if doc.id not in enrolled_subject_ids:
                subject_data = doc.to_dict()
                subject_data['id'] = doc.id
                
                # Calculate topic count
                topics_ref = db.collection('topics').where('subject_id', '==', doc.id)
                topic_count = len(list(topics_ref.stream()))
                subject_data['topic_count'] = topic_count
                
                # Calculate quiz count
                quizzes_ref = db.collection('quizzes').where('subject_id', '==', doc.id).where('is_published', '==', True)
                quiz_count = len(list(quizzes_ref.stream()))
                subject_data['quiz_count'] = quiz_count
                
                available_subjects.append(subject_data)
        
        return render_template('browse_subjects.html', 
                             available_subjects=available_subjects,
                             username=session.get('username'))
    
    except Exception as e:
        print(f"Error fetching available subjects: {e}")
        flash('Error loading subjects.')
        return redirect(url_for('dashboard'))

# Add these routes to your Flask app

@app.route('/search')
def search_profiles():
    """Search for user profiles"""
    if 'user_id' not in session:
        flash('Please log in to search profiles.')
        return redirect(url_for('login'))
    
    query = request.args.get('q', '').strip()
    role_filter = request.args.get('role', '')  # 'student', 'teacher', or empty for all
    page = int(request.args.get('page', 1))
    per_page = 12
    
    results = []
    total_results = 0
    
    if query:
        try:
            # Get all users from Firestore
            users_ref = db.collection('users')
            all_users = list(users_ref.stream())
            
            # Filter users based on query and role
            filtered_users = []
            for user_doc in all_users:
                user_data = user_doc.to_dict()
                user_data['id'] = user_doc.id
                
                # Skip current user
                if user_data['id'] == session['user_id']:
                    continue
                
                # Role filter
                if role_filter and user_data.get('role', '') != role_filter:
                    continue
                
                # Search in username, full_name, email, institution
                search_fields = [
                    user_data.get('username', '').lower(),
                    user_data.get('full_name', '').lower(),
                    user_data.get('email', '').lower(),
                    user_data.get('institution', '').lower()
                ]
                
                query_lower = query.lower()
                if any(query_lower in field for field in search_fields):
                    filtered_users.append(user_data)
            
            total_results = len(filtered_users)
            
            # Implement pagination
            start_idx = (page - 1) * per_page
            end_idx = start_idx + per_page
            results = filtered_users[start_idx:end_idx]
            
            # Calculate pagination info
            total_pages = (total_results + per_page - 1) // per_page
            has_prev = page > 1
            has_next = page < total_pages
            prev_num = page - 1 if has_prev else None
            next_num = page + 1 if has_next else None
            
        except Exception as e:
            print(f"Error searching profiles: {e}")
            flash('Error occurred while searching profiles.')
            results = []
            total_results = 0
            total_pages = 0
            has_prev = False
            has_next = False
            prev_num = None
            next_num = None
    else:
        total_pages = 0
        has_prev = False
        has_next = False
        prev_num = None
        next_num = None
    
    return render_template('search_results.html',
                         results=results,
                         query=query,
                         role_filter=role_filter,
                         total_results=total_results,
                         page=page,
                         total_pages=total_pages,
                         has_prev=has_prev,
                         has_next=has_next,
                         prev_num=prev_num,
                         next_num=next_num,
                         username=session.get('username'),
                         role=session.get('role'))

@app.route('/user/<user_id>')
def view_user_profile(user_id):
    """View another user's public profile"""
    if 'user_id' not in session:
        flash('Please log in to view profiles.')
        return redirect(url_for('login'))
    
    # Prevent users from viewing their own profile through this route
    if user_id == session['user_id']:
        return redirect(url_for('profile'))
    
    try:
        # Get user data
        user_doc = db.collection('users').document(user_id).get()
        if not user_doc.exists:
            flash('User not found.')
            return redirect(url_for('search_profiles'))
        
        user_data = user_doc.to_dict()
        user_data['id'] = user_doc.id
        
        # Create user object
        class PublicUserProfile:
            def __init__(self, data):
                self.id = data.get('id')
                self.username = data.get('username', '')
                self.full_name = data.get('full_name', '')
                self.role = data.get('role', '')
                self.bio = data.get('bio', '')
                self.institution = data.get('institution', '')
                self.avatar_type = data.get('avatar_type', 'initial')
                self.avatar_id = data.get('avatar_id', 'blue')
                self.created_at = data.get('created_at', None)
        
        viewed_user = PublicUserProfile(user_data)
        
        # Get public statistics and activities
        stats = {}
        recent_activities = []
        subjects = []
        
        if viewed_user.role == 'teacher':
            # Get teacher's subjects
            subjects_query = db.collection('subjects').where('teacher_id', '==', user_id)
            subjects_docs = list(subjects_query.stream())
            
            for subject_doc in subjects_docs:
                subject_data = subject_doc.to_dict()
                subject_data['id'] = subject_doc.id
                subjects.append(subject_data)
            
            # Calculate teacher stats
            quizzes_query = db.collection('quizzes').where('teacher_id', '==', user_id)
            quizzes_docs = list(quizzes_query.stream())
            quizzes_count = len(quizzes_docs)
            
            # Get enrolled students count
            enrollments_query = db.collection('enrollments').where('teacher_id', '==', user_id).where('status', '==', 'active')
            enrolled_students_docs = list(enrollments_query.stream())
            total_students = len(set(doc.to_dict()['student_id'] for doc in enrolled_students_docs))
            
            # Calculate total quiz attempts on teacher's quizzes
            total_attempts = 0
            total_score = 0
            
            for quiz_doc in quizzes_docs:
                quiz_id = quiz_doc.id
                attempts_query = db.collection('quiz_attempts').where('quiz_id', '==', quiz_id)
                attempts_docs = list(attempts_query.stream())
                
                for attempt_doc in attempts_docs:
                    attempt_data = attempt_doc.to_dict()
                    total_attempts += 1
                    total_score += attempt_data.get('percentage', 0)
            
            avg_score = (total_score / total_attempts) if total_attempts > 0 else 0
            
            stats = {
                'subjects_count': len(subjects),
                'quizzes_count': quizzes_count,
                'total_students': total_students,
                'total_attempts': total_attempts,
                'avg_score': round(avg_score, 1) if avg_score > 0 else 0,
                'teaching_experience': calculate_teaching_experience(viewed_user.created_at)
            }
            
        else:  # Student
            # Get student's enrollments and quiz attempts
            attempts_query = db.collection('quiz_attempts').where('user_id', '==', user_id)
            attempts_docs = list(attempts_query.stream())
            
            enrollments_query = db.collection('enrollments').where('student_id', '==', user_id).where('status', '==', 'active')
            enrollments_docs = list(enrollments_query.stream())
            
            # Get subjects the student is enrolled in
            for enrollment_doc in enrollments_docs:
                enrollment_data = enrollment_doc.to_dict()
                subjects.append({
                    'name': enrollment_data.get('subject_name', ''),
                    'teacher_name': enrollment_data.get('teacher_name', ''),
                    'enrolled_at': enrollment_data.get('enrolled_at', '')
                })
            
            quizzes_taken = len(attempts_docs)
            total_score = sum(attempt.to_dict().get('percentage', 0) for attempt in attempts_docs)
            average_score = (total_score / quizzes_taken) if quizzes_taken > 0 else 0
            
            stats = {
                'quizzes_taken': quizzes_taken,
                'average_score': round(average_score, 1),
                'subjects_enrolled': len(subjects),
                'learning_streak': calculate_learning_streak(user_id),
                'member_since': calculate_member_duration(viewed_user.created_at)
            }
        
        # Get recent public activities (last 10)
        recent_activities = get_public_recent_activities(user_id, viewed_user.role)
        
        return render_template('user_profile.html',
                             viewed_user=viewed_user,
                             stats=stats,
                             recent_activities=recent_activities,
                             subjects=subjects,
                             username=session.get('username'),
                             role=session.get('role'))
    
    except Exception as e:
        print(f"Error loading user profile: {e}")
        import traceback
        traceback.print_exc()
        flash('Error loading user profile.')
        return redirect(url_for('search_profiles'))

# Helper functions
def calculate_teaching_experience(created_at):
    """Calculate teaching experience in months"""
    if not created_at:
        return 0
    
    from datetime import datetime
    if hasattr(created_at, 'timestamp'):
        created_date = datetime.fromtimestamp(created_at.timestamp())
    else:
        created_date = created_at
    
    now = datetime.now()
    diff = now - created_date
    months = diff.days // 30
    return max(1, months)

def calculate_member_duration(created_at):
    """Calculate how long user has been a member"""
    if not created_at:
        return "Recently joined"
    
    from datetime import datetime
    if hasattr(created_at, 'timestamp'):
        created_date = datetime.fromtimestamp(created_at.timestamp())
    else:
        created_date = created_at
    
    now = datetime.now()
    diff = now - created_date
    
    if diff.days < 30:
        return "Recently joined"
    elif diff.days < 365:
        months = diff.days // 30
        return f"{months} month{'s' if months > 1 else ''} ago"
    else:
        years = diff.days // 365
        return f"{years} year{'s' if years > 1 else ''} ago"

def calculate_learning_streak(user_id):
    """Calculate current learning streak for student"""
    try:
        # Get recent quiz attempts ordered by date
        attempts_query = db.collection('quiz_attempts')\
            .where('user_id', '==', user_id)\
            .order_by('created_at', direction=firestore.Query.DESCENDING)\
            .limit(30)  # Check last 30 attempts
        
        attempts = list(attempts_query.stream())
        
        if not attempts:
            return 0
        
        # Calculate consecutive days with quiz attempts
        from datetime import datetime, timedelta
        
        streak = 0
        current_date = datetime.now().date()
        
        # Group attempts by date
        attempts_by_date = {}
        for attempt in attempts:
            attempt_data = attempt.to_dict()
            attempt_date = attempt_data.get('created_at')
            if attempt_date:
                if hasattr(attempt_date, 'timestamp'):
                    date_key = datetime.fromtimestamp(attempt_date.timestamp()).date()
                else:
                    date_key = attempt_date.date()
                attempts_by_date[date_key] = True
        
        # Count consecutive days
        check_date = current_date
        while check_date in attempts_by_date:
            streak += 1
            check_date -= timedelta(days=1)
        
        return streak
    
    except Exception as e:
        print(f"Error calculating learning streak: {e}")
        return 0

def get_public_recent_activities(user_id, user_role, limit=10):
    """Get public recent activities for a user"""
    activities = []
    try:
        if user_role == 'teacher':
            # Get recent enrollments in teacher's subjects
            enrollments_query = db.collection('enrollments')\
                .where('teacher_id', '==', user_id)\
                .order_by('enrolled_at', direction=firestore.Query.DESCENDING)\
                .limit(limit)
            
            enrollments = list(enrollments_query.stream())
            for enrollment in enrollments:
                data = enrollment.to_dict()
                activities.append({
                    'type': 'enrollment',
                    'description': f"New student enrolled in {data.get('subject_name', 'a subject')}",
                    'created_at': data.get('enrolled_at', datetime.now()),
                    'icon': 'user-plus'
                })
            
            # Get recent quiz creations
            quizzes_query = db.collection('quizzes')\
                .where('teacher_id', '==', user_id)\
                .order_by('created_at', direction=firestore.Query.DESCENDING)\
                .limit(5)
            
            quizzes = list(quizzes_query.stream())
            for quiz in quizzes:
                data = quiz.to_dict()
                activities.append({
                    'type': 'quiz_created',
                    'description': f"Created quiz: {data.get('title', 'Untitled Quiz')}",
                    'created_at': data.get('created_at', datetime.now()),
                    'icon': 'plus-circle'
                })
        
        else:  # Student
            # Get recent quiz attempts
            attempts_query = db.collection('quiz_attempts')\
                .where('user_id', '==', user_id)\
                .order_by('created_at', direction=firestore.Query.DESCENDING)\
                .limit(limit)
            
            attempts = list(attempts_query.stream())
            for attempt in attempts:
                data = attempt.to_dict()
                score = data.get('percentage', 0)
                activities.append({
                    'type': 'quiz_attempt',
                    'description': f"Completed a quiz with {score}% score",
                    'created_at': data.get('created_at', datetime.now()),
                    'icon': 'check-circle' if score >= 70 else 'x-circle',
                    'score': score
                })
            
            # Get recent enrollments
            enrollments_query = db.collection('enrollments')\
                .where('student_id', '==', user_id)\
                .order_by('enrolled_at', direction=firestore.Query.DESCENDING)\
                .limit(5)
            
            enrollments = list(enrollments_query.stream())
            for enrollment in enrollments:
                data = enrollment.to_dict()
                activities.append({
                    'type': 'enrollment',
                    'description': f"Enrolled in {data.get('subject_name', 'a subject')}",
                    'created_at': data.get('enrolled_at', datetime.now()),
                    'icon': 'book-open'
                })
        
        # Sort by date and limit
        activities.sort(key=lambda x: x['created_at'], reverse=True)
        return activities[:limit]
    
    except Exception as e:
        print(f"Error getting public activities: {e}")
        return []


# ...existing code...


# Helper: create a notification for a user
# Updated create_notification function with better error handling
def create_notification(
    user_id,
    title,
    message,
    notif_type='info',
    link_url=None,
    icon='bell',
    metadata=None,
    actor_id=None,
    actor_name=None
):
    try:
        from firebase_admin import firestore
        
        data = {
            'user_id': str(user_id),  # Ensure it's a string
            'title': title,
            'message': message,
            'type': notif_type,
            'link_url': link_url,
            'icon': icon,
            'metadata': metadata or {},
            'actor_id': actor_id,
            'actor_name': actor_name,
            'read': False,
            'created_at': firestore.SERVER_TIMESTAMP  # Use server timestamp
        }
        
        print(f"Debug: Creating notification for user {user_id}")  # Debug
        
        doc_ref, doc = db.collection('notifications').add(data)
        print(f"Debug: Notification created with ID: {doc.id}")  # Debug
        return doc.id
        
    except Exception as e:
        print(f"Error creating notification: {e}")
        import traceback
        traceback.print_exc()  # Print full stack trace
        return None

# Updated notifications route with better debugging
@app.route('/notifications')
def notifications():
    if 'user_id' not in session:
        flash('Please log in to view notifications.')
        return redirect(url_for('login'))

    user_id = session['user_id']
    print(f"Debug: Loading notifications for user: {user_id}")

    try:
        # Fetch latest 50 notifications for current user
        query = (db.collection('notifications')
                   .where('user_id', '==', str(user_id))  # Ensure string comparison
                   .order_by('created_at', direction=firestore.Query.DESCENDING)
                   .limit(50))
        
        docs = list(query.stream())
        print(f"Debug: Found {len(docs)} notifications")  # Debug
        
        items = []
        unread_count = 0
        
        for d in docs:
            try:
                n = d.to_dict()
                n['id'] = d.id
                
                if not n.get('read'):
                    unread_count += 1
                
                # Handle timestamp conversion
                created_at = n.get('created_at')
                if created_at:
                    try:
                        if hasattr(created_at, 'timestamp'):
                            # Firestore timestamp
                            dt = created_at
                            n['created_at'] = dt.strftime('%Y-%m-%d %H:%M')
                        elif hasattr(created_at, 'strftime'):
                            # Python datetime
                            n['created_at'] = created_at.strftime('%Y-%m-%d %H:%M')
                        else:
                            n['created_at'] = str(created_at)
                    except Exception as te:
                        print(f"Timestamp error: {te}")
                        n['created_at'] = 'Unknown'
                else:
                    n['created_at'] = 'Unknown'
                
                items.append(n)
                print(f"Debug: Added notification: {n['title']}")  # Debug
                
            except Exception as doc_error:
                print(f"Error processing document {d.id}: {doc_error}")
                continue

        print(f"Debug: Returning {len(items)} notifications, {unread_count} unread")
        
        return render_template('notification.html',
                               notifications=items,
                               unread_count=unread_count,
                               username=session.get('username'),
                               role=session.get('role'))
                               
    except Exception as e:
        print(f"Error loading notifications: {e}")
        import traceback
        traceback.print_exc()
        flash('Error loading notifications.')
        return redirect(url_for('dashboard'))

# Test function to manually create a notification
@app.route('/test-notification')
def test_notification():
    if 'user_id' not in session:
        return "Please log in first"
    
    # Create a test notification
    notif_id = create_notification(
        user_id=session['user_id'],
        title='Test Notification',
        message='This is a test notification to verify the system works.',
        notif_type='test',
        icon='test'
    )
    
    if notif_id:
        return f"Test notification created with ID: {notif_id}"
    else:
        return "Failed to create test notification"

@app.route('/notifications/<notification_id>/read', methods=['POST'])
def mark_notification_read(notification_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please log in'}), 401
    try:
        ref = db.collection('notifications').document(notification_id)
        doc = ref.get()
        if not doc.exists:
            return jsonify({'success': False, 'message': 'Not found'}), 404
        data = doc.to_dict()
        if data.get('user_id') != session['user_id']:
            return jsonify({'success': False, 'message': 'Forbidden'}), 403
        ref.update({'read': True, 'read_at': datetime.now()})
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error marking notification read: {e}")
        return jsonify({'success': False, 'message': 'Error'}), 500

@app.route('/notifications/mark-all-read', methods=['POST'])
def mark_all_notifications_read():
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please log in'}), 401
    try:
        batch = db.batch()
        q = (db.collection('notifications')
               .where('user_id', '==', session['user_id'])
               .where('read', '==', False))
        docs = list(q.stream())
        for d in docs:
            batch.update(d.reference, {'read': True, 'read_at': datetime.now()})
        if docs:
            batch.commit()
        return jsonify({'success': True, 'updated': len(docs)})
    except Exception as e:
        print(f"Error marking all notifications read: {e}")
        return jsonify({'success': False, 'message': 'Error'}), 500

# Add these routes to your app.py file

@app.route('/notifications/<notification_id>', methods=['DELETE'])
def delete_notification(notification_id):
    """Delete a single notification"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please log in'}), 401
    
    try:
        # Get the notification document
        ref = db.collection('notifications').document(notification_id)
        doc = ref.get()
        
        if not doc.exists:
            return jsonify({'success': False, 'message': 'Notification not found'}), 404
        
        # Check if the notification belongs to the current user
        data = doc.to_dict()
        if data.get('user_id') != str(session['user_id']):
            return jsonify({'success': False, 'message': 'Forbidden'}), 403
        
        # Delete the notification
        ref.delete()
        print(f"Debug: Deleted notification {notification_id} for user {session['user_id']}")
        
        return jsonify({'success': True, 'message': 'Notification deleted'})
        
    except Exception as e:
        print(f"Error deleting notification: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': 'Error deleting notification'}), 500


@app.route('/notifications/delete-all', methods=['DELETE'])
def delete_all_notifications():
    """Delete all notifications for the current user"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please log in'}), 401
    
    try:
        # Get all notifications for the current user
        query = (db.collection('notifications')
                   .where('user_id', '==', str(session['user_id'])))
        
        docs = list(query.stream())
        
        if not docs:
            return jsonify({'success': True, 'message': 'No notifications to delete', 'deleted': 0})
        
        # Use batch operation for better performance
        batch = db.batch()
        
        for doc in docs:
            batch.delete(doc.reference)
        
        # Commit the batch operation
        batch.commit()
        
        deleted_count = len(docs)
        print(f"Debug: Deleted {deleted_count} notifications for user {session['user_id']}")
        
        return jsonify({
            'success': True, 
            'message': f'Deleted {deleted_count} notifications',
            'deleted': deleted_count
        })
        
    except Exception as e:
        print(f"Error deleting all notifications: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': 'Error deleting notifications'}), 500


# Optional: Add a route to delete only read notifications
@app.route('/notifications/delete-read', methods=['DELETE'])
def delete_read_notifications():
    """Delete all read notifications for the current user"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please log in'}), 401
    
    try:
        # Get all read notifications for the current user
        query = (db.collection('notifications')
                   .where('user_id', '==', str(session['user_id']))
                   .where('read', '==', True))
        
        docs = list(query.stream())
        
        if not docs:
            return jsonify({'success': True, 'message': 'No read notifications to delete', 'deleted': 0})
        
        # Use batch operation for better performance
        batch = db.batch()
        
        for doc in docs:
            batch.delete(doc.reference)
        
        # Commit the batch operation
        batch.commit()
        
        deleted_count = len(docs)
        print(f"Debug: Deleted {deleted_count} read notifications for user {session['user_id']}")
        
        return jsonify({
            'success': True, 
            'message': f'Deleted {deleted_count} read notifications',
            'deleted': deleted_count
        })
        
    except Exception as e:
        print(f"Error deleting read notifications: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': 'Error deleting notifications'}), 500

@app.route('/games/codingpower')
def codingpower():
    return render_template('games/codingpower.html')

@app.route('/game/<game_id>')
def serve_game(game_id):
    """Serve individual game pages"""
    if not session.get('user_id'):
        flash('Please log in to play games.', 'error')
        return redirect(url_for('login'))
    
    # Map game IDs to their template files
    games = {
        'fruit_catch_game': 'games/fruit_catch.html',
        'typing_speed_test': 'games/typing_speed_test.html', 
        'code_debugger_game': 'games/code_debugger.html'
        
    }
    
    if game_id in games:
        return render_template(games[game_id])
    else:
        flash(f'Game "{game_id}" not found.', 'error')
        return redirect(url_for('dashboard'))  # or wherever your games are listed

# Optional: Keep the old /run/<game_name> route for backward compatibility
@app.route('/run/<game_name>')
def run_game(game_name):
    """Legacy route - redirects to the new game route"""
    # Map old game names to new game IDs
    game_mapping = {
        "Game Fruit Catch": "fruit_catch_game",
        "Typing Speed Test": "typing_speed_test",
        "Code Debugger": "code_debugger_game"
    }
    
    if game_name in game_mapping:
        return redirect(url_for('serve_game', game_id=game_mapping[game_name]))
    else:
        flash(f'Game "{game_name}" not found.', 'error')
        return redirect(url_for('dashboard'))  # or wherever your games are listed
    
@app.route('/games')
def view_games():
    """Display the games page - works with your existing route structure"""
    try:
        # Get session data (adapt to your session structure)
        username = session.get('username', 'User')
        role = session.get('role', 'student')
        user_id = session.get('user_id')
        
        # If you don't have username in session but have user_id, you might need to query database
        if not username and user_id:
            # Query your database to get username from user_id
            # cursor = mysql.connection.cursor()
            # cursor.execute("SELECT username, role FROM users WHERE id = %s", (user_id,))
            # user_data = cursor.fetchone()
            # if user_data:
            #     username = user_data[0]
            #     role = user_data[1]
            # cursor.close()
            pass
        
        return render_template('view_games.html', 
                             username=session.get('username'), 
                             role=session.get('role'))
    except Exception as e:
        flash(f'Error loading games: {str(e)}', 'error')
        return redirect(url_for('dashboard'))
    
@app.route('/post_announcement', methods=['GET', 'POST'])
def post_announcement():
    # Check if user is logged in
    if 'user_id' not in session:
        flash('Please log in to access this page.', 'error')
        return redirect(url_for('login'))
   
    # Check if user is a teacher
    if session.get('role') != 'teacher':
        flash('Access denied. Teachers only.', 'error')
        return redirect(url_for('dashboard'))
   
    if request.method == 'POST':
        try:
            # Get form data
            title = request.form['title']
            content = request.form['content']
            priority = request.form.get('priority', 'normal')
            audience = request.form.get('audience', 'all')
            subject_id = request.form.get('subject_id') if audience == 'subject' else None
            send_email = 'send_email' in request.form
           
            # Create announcement document
            announcement_data = {
                'title': title,
                'content': content,
                'priority': priority,
                'audience': audience,
                'subject_id': subject_id,
                'teacher_id': session['user_id'],
                'teacher_name': session['username'],
                'send_email': send_email,
                'is_active': True,  # Add this line
                'created_at': datetime.now(timezone.utc),
                'updated_at': datetime.now(timezone.utc)
            }
           
            # Add to Firestore
            db.collection('announcements').add(announcement_data)
           
            flash('Announcement posted successfully!', 'success')
            return redirect(url_for('view_announcements'))
           
        except Exception as e:
            print(f"Error posting announcement: {e}")
            flash('Error posting announcement. Please try again.', 'error')
   
    # Get subjects for the current teacher
    try:
        subjects_ref = db.collection('subjects').where('teacher_id', '==', session['user_id'])
        subjects = []
        for doc in subjects_ref.stream():
            subject_data = doc.to_dict()
            subject_data['id'] = doc.id
            subjects.append(subject_data)
    except Exception as e:
        print(f"Error fetching subjects: {e}")
        subjects = []
   
    # Pass all necessary variables to the template
    return render_template('post_announcement.html',
                         subjects=subjects,
                         username=session.get('username'),
                         role=session.get('role'),
                         user_id=session.get('user_id'))
   
@app.route('/announcements')
def view_announcements():
    if 'user_id' not in session:
        flash('Please log in to view announcements.')
        return redirect(url_for('login'))
   
    print(f"DEBUG: Loading announcements for user {session['user_id']} with role {session.get('role')}")
   
    announcements = []
    try:
        user_role = session.get('role')
        user_id = session['user_id']
       
        if user_role == 'teacher':
            # Teachers see their own announcements
            print(f"DEBUG: Loading teacher announcements for user_id: {user_id}")
            announcements_ref = db.collection('announcements').where('teacher_id', '==', user_id)
           
            # Get all docs first, then sort in Python
            teacher_announcements = []
            for doc in announcements_ref.stream():
                announcement_data = doc.to_dict()
                announcement_data['id'] = doc.id
               
                # Set default is_active if missing
                if 'is_active' not in announcement_data:
                    announcement_data['is_active'] = True
               
                teacher_announcements.append(announcement_data)
                print(f"DEBUG: Found teacher announcement: {announcement_data.get('title')}")
           
            # Sort by created_at in Python
            announcements = sorted(teacher_announcements,
                                 key=lambda x: x.get('created_at', datetime.min),
                                 reverse=True)
       
        elif user_role == 'student':
            # Students see announcements they should see
            print(f"DEBUG: Loading student announcements")
           
            # Get student's enrolled subjects
            enrolled_subject_ids = []
            try:
                enrollments_ref = db.collection('enrollments').where('student_id', '==', user_id).where('status', '==', 'active')
                for enrollment_doc in enrollments_ref.stream():
                    enrollment_data = enrollment_doc.to_dict()
                    enrolled_subject_ids.append(enrollment_data['subject_id'])
                print(f"DEBUG: Student enrolled in subjects: {enrolled_subject_ids}")
            except Exception as e:
                print(f"Error getting enrollments: {e}")
                enrolled_subject_ids = []
           
            # Get ALL announcements first (not just active ones since we need to check each one)
            all_announcements_ref = db.collection('announcements')
           
            student_announcements = []
            for doc in all_announcements_ref.stream():
                announcement_data = doc.to_dict()
                announcement_data['id'] = doc.id
               
                # Set default is_active if missing
                if 'is_active' not in announcement_data:
                    announcement_data['is_active'] = True
               
                # Only include active announcements
                if not announcement_data.get('is_active', True):
                    continue
               
                audience = announcement_data.get('audience', 'all')
               
                # Include announcement if:
                # 1. It's for everyone (audience == 'all')
                # 2. It's subject-specific and student is enrolled in that subject
                if audience == 'all':
                    student_announcements.append(announcement_data)
                    print(f"DEBUG: Added general announcement: {announcement_data.get('title')}")
                elif audience == 'subject':
                    announcement_subject_id = announcement_data.get('subject_id')
                    if announcement_subject_id in enrolled_subject_ids:
                        student_announcements.append(announcement_data)
                        print(f"DEBUG: Added subject announcement: {announcement_data.get('title')}")
           
            # Sort by created_at in Python
            announcements = sorted(student_announcements,
                                 key=lambda x: x.get('created_at', datetime.min),
                                 reverse=True)
       
        print(f"DEBUG: Total announcements found: {len(announcements)}")
       
    except Exception as e:
        print(f"ERROR fetching announcements: {e}")
        import traceback
        traceback.print_exc()
        flash('Error loading announcements.')
        announcements = []  # Ensure we have a default value
   
    return render_template('view_announcements.html',
                         announcements=announcements,
                         role=user_role,
                         username=session.get('username'))
   
@app.route('/announcement/<announcement_id>/toggle', methods=['POST'])
def toggle_announcement(announcement_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        flash('Access denied.')
        return redirect(url_for('dashboard'))
   
    try:
        announcement_ref = db.collection('announcements').document(announcement_id)
        announcement_doc = announcement_ref.get()
       
        if not announcement_doc.exists:
            flash('Announcement not found.')
            return redirect(url_for('view_announcements'))
       
        announcement_data = announcement_doc.to_dict()
       
        # Verify ownership
        if announcement_data.get('teacher_id') != session['user_id']:
            flash('Access denied.')
            return redirect(url_for('view_announcements'))
       
        # Toggle active status
        new_status = not announcement_data.get('is_active', True)
        announcement_ref.update({
            'is_active': new_status,
            'updated_at': datetime.now()
        })
       
        status_text = "activated" if new_status else "deactivated"
        flash(f'Announcement {status_text} successfully.')
       
    except Exception as e:
        print(f"Error toggling announcement: {e}")
        flash('Error updating announcement status.')
   
    return redirect(url_for('view_announcements'))


@app.route('/announcement/<announcement_id>/delete', methods=['POST'])
def delete_announcement(announcement_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        flash('Access denied.')
        return redirect(url_for('dashboard'))
   
    try:
        announcement_ref = db.collection('announcements').document(announcement_id)
        announcement_doc = announcement_ref.get()
       
        if not announcement_doc.exists:
            flash('Announcement not found.')
            return redirect(url_for('view_announcements'))
       
        announcement_data = announcement_doc.to_dict()
       
        # Verify ownership
        if announcement_data.get('teacher_id') != session['user_id']:
            flash('Access denied.')
            return redirect(url_for('view_announcements'))
       
        # Delete the announcement
        announcement_ref.delete()
        flash('Announcement deleted successfully.')
       
    except Exception as e:
        print(f"Error deleting announcement: {e}")
        flash('Error deleting announcement.')
   
    return redirect(url_for('view_announcements'))


@app.route('/announcement/<announcement_id>/comments/count')
def get_announcement_comments_count(announcement_id):
    """Get comment count for an announcement"""
    try:
        comments_ref = db.collection('announcement_comments').where('announcement_id', '==', announcement_id)
        comments_count = len(list(comments_ref.stream()))
       
        return jsonify({'success': True, 'count': comments_count})
       
    except Exception as e:
        print(f"Error loading comment count: {e}")
        return jsonify({'success': False, 'message': f'Error loading comment count: {str(e)}'}), 500


@app.route('/announcement/comment/<comment_id>/delete', methods=['DELETE'])
def delete_announcement_comment(comment_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please log in to delete comments'}), 401
   
    try:
        # Get the comment document
        comment_ref = db.collection('announcement_comments').document(comment_id)
        comment_doc = comment_ref.get()
       
        if not comment_doc.exists:
            return jsonify({'success': False, 'message': 'Comment not found'}), 404
       
        comment_data = comment_doc.to_dict()
       
        # Check if the comment belongs to the current user
        if comment_data.get('user_id') != session['user_id']:
            return jsonify({'success': False, 'message': 'You can only delete your own comments'}), 403
       
        # Delete the comment
        comment_ref.delete()
       
        return jsonify({'success': True, 'message': 'Comment deleted successfully'})
       
    except Exception as e:
        print(f"Error deleting comment: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Error deleting comment: {str(e)}'}), 500


@app.route('/announcement/<announcement_id>/comments')
def get_announcement_comments(announcement_id):
    try:
        comments_ref = db.collection('announcement_comments').where('announcement_id', '==', announcement_id)
        comments = []
       
        for doc in comments_ref.stream():
            data = doc.to_dict()
            data['id'] = doc.id
           
            # Add ownership information
            data['is_owner'] = data.get('user_id') == session.get('user_id') if 'user_id' in session else False
           
            # Format date for display
            created_at = data.get('created_at')
            if created_at:
                try:
                    if hasattr(created_at, 'strftime'):
                        data['created_at'] = created_at.strftime('%b %d, %Y %I:%M %p')
                    else:
                        data['created_at'] = str(created_at)
                except Exception:
                    data['created_at'] = 'just posted'
            else:
                data['created_at'] = 'just posted'
               
            comments.append(data)
       
        # Sort comments by created_at descending (newest first)
        comments.sort(key=lambda x: x.get('created_at', ''), reverse=True)
       
        return jsonify({'success': True, 'comments': comments})
       
    except Exception as e:
        print(f"Error loading comments: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Error loading comments: {str(e)}'}), 500


@app.route('/announcement/<announcement_id>/comment', methods=['POST'])
def post_announcement_comment(announcement_id):
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Please log in to post comments'}), 401
   
    try:
        content = request.form.get('content', '').strip()
        if not content:
            return jsonify({'success': False, 'message': 'Comment cannot be empty'}), 400


        # Create comment data
        comment_data = {
            'announcement_id': announcement_id,
            'user_id': session['user_id'],
            'username': session.get('username', 'Anonymous'),
            'content': content,
            'created_at': datetime.now()
        }
       
        # Add to database
        doc_ref = db.collection('announcement_comments').add(comment_data)
        comment_id = doc_ref[1].id
       
        # Return the comment data for immediate display
        return jsonify({
            'success': True,
            'message': 'Comment posted successfully',
            'comment': {
                'id': comment_id,
                'content': content,
                'username': session.get('username', 'Anonymous'),
                'created_at': 'just now',
                'is_owner': True
            }
        })
       
    except Exception as e:
        print(f"Error posting comment: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': f'Error posting comment: {str(e)}'}), 500



# ...existing code...

@app.route('/subject/<subject_id>/announcements')
def subject_announcement(subject_id):
    if 'user_id' not in session:
        flash('Please log in to view announcements.')
        return redirect(url_for('login'))
    
    try:
        # Get subject details first
        subject_ref = db.collection('subjects').document(subject_id)
        subject_doc = subject_ref.get()
        
        if not subject_doc.exists:
            flash('Subject not found.')
            return redirect(url_for('dashboard'))
        
        subject_data = subject_doc.to_dict()
        subject_data['id'] = subject_doc.id
        
        # Check access permissions
        user_role = session.get('role')
        user_id = session['user_id']
        
        # For students, check if they're enrolled in this subject
        if user_role == 'student':
            is_enrolled = check_enrollment(user_id, subject_id)
            if not is_enrolled:
                flash('You must be enrolled in this subject to view its announcements.')
                return redirect(url_for('view_subject', subject_id=subject_id))
        
        # For teachers, check if they own this subject
        elif user_role == 'teacher' and subject_data.get('teacher_id') != user_id:
            flash('Access denied.')
            return redirect(url_for('dashboard'))
        
        print(f"DEBUG: Loading announcements for subject {subject_id} for user {user_id} with role {user_role}")
        
        # Get announcements specific to this subject - FIXED QUERY
        announcements = []
        announcements_ref = db.collection('announcements')\
            .where('subject_id', '==', subject_id)\
            .where('is_active', '==', True)
        
        # Get all documents first, then sort in Python (since Firestore has limitations with compound queries)
        announcements_docs = []
        for doc in announcements_ref.stream():
            announcement_data = doc.to_dict()
            announcement_data['id'] = doc.id
            
            # Format the created_at timestamp
            if announcement_data.get('created_at'):
                try:
                    if hasattr(announcement_data['created_at'], 'timestamp'):
                        announcement_data['created_at'] = datetime.fromtimestamp(
                            announcement_data['created_at'].timestamp()
                        )
                except Exception as e:
                    print(f"Error formatting timestamp: {e}")
            
            announcements_docs.append(announcement_data)
        
        # Sort by created_at in Python (newest first)
        announcements = sorted(announcements_docs, 
                             key=lambda x: x.get('created_at', datetime.min), 
                             reverse=True)
        
        print(f"DEBUG: Found {len(announcements)} announcements for subject {subject_id}")
        
        return render_template('subject_announcement.html',
                             announcements=announcements,
                             subject=subject_data,
                             role=user_role,
                             username=session.get('username'))
        
    except Exception as e:
        print(f"ERROR fetching subject announcements: {e}")
        import traceback
        traceback.print_exc()
        flash('Error loading announcements.')
        return redirect(url_for('view_subject', subject_id=subject_id))


# ...existing code...

# Add these enhanced chat routes to your existing app.py

# ============================================
# ENHANCED CHAT ROUTES WITH PERSISTENCE
# ============================================

@app.route('/chat')
def chat():
    """Main chat page with persistent message support"""
    if 'user_id' not in session:
        flash('Please log in to access chat.', 'error')
        return redirect(url_for('login'))
    
    return render_template('chat.html',
                         username=session.get('username'),
                         role=session.get('role'),
                         user_id=session.get('user_id'))

def get_conversation_id(a, b):
    """Deterministic DM room id for a pair of users"""
    a, b = str(a), str(b)
    return f"dm:{a}:{b}" if a <= b else f"dm:{b}:{a}"

@app.route('/api/chat/history')
def get_chat_history():
    """Get chat history from Firestore - persists across sessions, logout, and offline"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        peer_id = request.args.get('peer_id')
        if not peer_id:
            return jsonify({'success': False, 'error': 'peer_id required'}), 400
        
        current_user_id = str(session['user_id'])
        peer_id = str(peer_id)
        conversation_id = get_conversation_id(current_user_id, peer_id)
        
        print(f"📨 Loading chat history for conversation: {conversation_id}")
        
        # Fetch messages from Firestore with improved query
        messages_ref = db.collection('direct_messages')\
            .where('conversation_id', '==', conversation_id)\
            .order_by('timestamp', direction=firestore.Query.ASCENDING)\
            .limit(500)  # Last 500 messages
        
        messages = []
        for doc in messages_ref.stream():
            msg_data = doc.to_dict()
            msg_data['id'] = doc.id
            
            # Convert Firestore timestamp to ISO format
            timestamp = msg_data.get('timestamp')
            if timestamp:
                try:
                    if hasattr(timestamp, 'isoformat'):
                        msg_data['timestamp'] = timestamp.isoformat()
                    elif hasattr(timestamp, 'timestamp'):
                        msg_data['timestamp'] = datetime.fromtimestamp(timestamp.timestamp()).isoformat()
                    else:
                        msg_data['timestamp'] = datetime.now().isoformat()
                except Exception as e:
                    print(f"Timestamp conversion error: {e}")
                    msg_data['timestamp'] = datetime.now().isoformat()
            else:
                msg_data['timestamp'] = datetime.now().isoformat()
            
            messages.append(msg_data)
        
        print(f"✅ Loaded {len(messages)} messages from database")
        
        return jsonify({
            'success': True,
            'messages': messages,
            'conversation_id': conversation_id
        })
    except Exception as e:
        print(f"❌ Error fetching chat history: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'error': str(e)}), 500

@app.route('/api/chat/mark-read', methods=['POST'])
def mark_messages_read():
    """Mark messages as read"""
    if 'user_id' not in session:
        return jsonify({'success': False}), 401
    
    try:
        data = request.get_json()
        conversation_id = data.get('conversation_id')
        
        if not conversation_id:
            return jsonify({'success': False}), 400
        
        # Update unread messages in this conversation
        messages_ref = db.collection('direct_messages')\
            .where('conversation_id', '==', conversation_id)\
            .where('to_user_id', '==', str(session['user_id']))\
            .where('read', '==', False)
        
        batch = db.batch()
        count = 0
        
        for doc in messages_ref.stream():
            batch.update(doc.reference, {
                'read': True,
                'read_at': firestore.SERVER_TIMESTAMP
            })
            count += 1
        
        if count > 0:
            batch.commit()
        
        return jsonify({'success': True, 'marked': count})
    except Exception as e:
        print(f"Error marking messages read: {e}")
        return jsonify({'success': False}), 500

@app.route('/api/chat/conversations')
def get_conversations():
    """Get list of conversations with last message preview"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        user_id = str(session['user_id'])
        
        # Get all conversations where user is a participant
        messages_ref = db.collection('direct_messages')
        
        # Query for messages where user is sender or receiver
        sent_messages = messages_ref.where('from_user_id', '==', user_id).stream()
        received_messages = messages_ref.where('to_user_id', '==', user_id).stream()
        
        # Collect unique conversation IDs
        conversations = {}
        
        for doc in sent_messages:
            msg_data = doc.to_dict()
            conv_id = msg_data.get('conversation_id')
            if conv_id:
                if conv_id not in conversations or msg_data.get('timestamp', datetime.min) > conversations[conv_id].get('timestamp', datetime.min):
                    conversations[conv_id] = msg_data
        
        for doc in received_messages:
            msg_data = doc.to_dict()
            conv_id = msg_data.get('conversation_id')
            if conv_id:
                if conv_id not in conversations or msg_data.get('timestamp', datetime.min) > conversations[conv_id].get('timestamp', datetime.min):
                    conversations[conv_id] = msg_data
        
        # Format conversations for response
        result = []
        for conv_id, last_msg in conversations.items():
            # Determine the other user
            other_user_id = last_msg.get('to_user_id') if last_msg.get('from_user_id') == user_id else last_msg.get('from_user_id')
            other_username = last_msg.get('to_username') if last_msg.get('from_user_id') == user_id else last_msg.get('from_username')
            
            result.append({
                'conversation_id': conv_id,
                'other_user_id': other_user_id,
                'other_username': other_username,
                'last_message': last_msg.get('content'),
                'timestamp': last_msg.get('timestamp').isoformat() if hasattr(last_msg.get('timestamp'), 'isoformat') else str(last_msg.get('timestamp'))
            })
        
        return jsonify({'success': True, 'conversations': result})
        
    except Exception as e:
        print(f"Error getting conversations: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ============================================
# SOCKET.IO HANDLERS WITH PERSISTENCE
# ============================================

# Socket.IO (Direct Messaging)
online_users = {}  # user_id -> {'username': str, 'role': str, 'sids': set()}
sid_to_user = {}   # sid -> user_id
user_message_timestamps = {}  # Rate limiting

def cleanup_rate_limiting():
    """Clean up old rate limiting entries"""
    try:
        current_time = time.time()
        for user_id, ts in list(user_message_timestamps.items()):
            if current_time - ts > 300:  # 5 minutes
                del user_message_timestamps[user_id]
    except Exception as e:
        print(f"Error in cleanup: {e}")

@socketio.on('connect')
def handle_connect():
    print(f'🔌 User connected: {request.sid}')
    cleanup_rate_limiting()

@socketio.on('disconnect')
def handle_disconnect():
    print(f'🔌 User disconnected: {request.sid}')
    sid = request.sid
    uid = sid_to_user.pop(sid, None)
    if not uid:
        return
    
    state = online_users.get(uid)
    if not state:
        return
    
    state['sids'].discard(sid)
    if not state['sids']:
        # Fully offline for this user
        user_data = online_users.pop(uid, None)
        user_message_timestamps.pop(uid, None)
        emit('user_left', {
            'user_id': uid,
            'username': (user_data or {}).get('username', ''),
            'online_users': [{'user_id': u, 'username': s['username'], 'role': s['role']} for u, s in online_users.items()]
        }, broadcast=True)
        print(f"👋 User {(user_data or {}).get('username','')} left chat")
    else:
        # Still has other sessions
        emit('online_users', {
            'users': [{'user_id': u, 'username': s['username'], 'role': s['role']} for u, s in online_users.items()]
        }, broadcast=True)

@socketio.on('join_chat')
def handle_join_chat(data):
    print(f"👤 User joining chat: {data}")
    try:
        uid = str(data.get('user_id') or '')
        username = (data.get('username') or '').strip()
        role = (data.get('role') or '').strip()
        
        if not uid or not username:
            emit('error', {'message': 'Missing required user data'})
            return

        sid_to_user[request.sid] = uid
        state = online_users.get(uid)
        
        if state:
            state['sids'].add(request.sid)
        else:
            online_users[uid] = {'username': username, 'role': role, 'sids': {request.sid}}
            emit('user_joined', {
                'user_id': uid,
                'username': username,
                'online_users': [{'user_id': u, 'username': s['username'], 'role': s['role']} for u, s in online_users.items()]
            }, broadcast=True)

        # Send current list to the joining client
        emit('online_users', {
            'users': [{'user_id': u, 'username': s['username'], 'role': s['role']} for u, s in online_users.items()]
        })
        
        print(f"✅ User {username} joined chat successfully")
        
    except Exception as e:
        print(f"❌ Error in join_chat: {e}")
        import traceback
        traceback.print_exc()
        emit('error', {'message': 'Failed to join chat'})

@socketio.on('join_dm')
def handle_join_dm(data):
    """Join a specific DM conversation room"""
    conversation_id = (data or {}).get('conversation_id')
    if conversation_id:
        join_room(conversation_id)
        print(f"💬 User joined conversation: {conversation_id}")

@socketio.on('typing_dm')
def handle_typing_dm(data):
    """Handle typing indicator"""
    conversation_id = (data or {}).get('conversation_id')
    uid = sid_to_user.get(request.sid)
    
    if conversation_id and uid:
        username = online_users.get(uid, {}).get('username')
        emit('user_typing_dm', {
            'conversation_id': conversation_id,
            'user_id': uid,
            'username': username
        }, room=conversation_id, include_self=False)

@socketio.on('stop_typing_dm')
def handle_stop_typing_dm(data):
    """Handle stop typing indicator"""
    conversation_id = (data or {}).get('conversation_id')
    if conversation_id:
        emit('user_stopped_typing_dm', {
            'conversation_id': conversation_id
        }, room=conversation_id, include_self=False)

@socketio.on('send_dm')
def handle_send_dm(data):
    """
    Enhanced send_dm with:
    - Firestore persistence (survives logout, refresh, offline)
    - Rate limiting
    - Delivery tracking
    - Read receipts support
    """
    try:
        uid = sid_to_user.get(request.sid)
        if not uid:
            emit('error', {'message': 'Please refresh and rejoin the chat'})
            return
        
        content = (data.get('content') or '').strip()
        to_user_id = str(data.get('to_user_id') or '')
        
        if not content or not to_user_id:
            return
        
        # Rate limit: 1 message per 0.5 seconds (adjusted for better UX)
        now = time.time()
        last = user_message_timestamps.get(uid, 0)
        if now - last < 0.5:
            emit('error', {'message': 'Please slow down'})
            return
        user_message_timestamps[uid] = now

        # Sanitize content (max 2000 characters)
        content = content[:2000]
        
        from_username = online_users.get(uid, {}).get('username') or data.get('from_username') or ''
        to_username = data.get('to_username') or ''
        conv_id = get_conversation_id(uid, to_user_id)

        # Prepare message data for Firestore
        message_data = {
            'conversation_id': conv_id,
            'from_user_id': str(uid),
            'from_username': from_username,
            'to_user_id': str(to_user_id),
            'to_username': to_username,
            'content': content,
            'timestamp': firestore.SERVER_TIMESTAMP,
            'read': False,
            'delivered': False
        }

        # Save to Firestore for PERMANENT storage
        try:
            doc_ref = db.collection('direct_messages').add(message_data)
            message_id = doc_ref[1].id
            
            # Use current time for real-time display
            now_iso = datetime.now(timezone.utc).isoformat()
            
            # Prepare message for socket emission
            socket_message = {
                'id': message_id,
                'conversation_id': conv_id,
                'from_user_id': str(uid),
                'from_username': from_username,
                'to_user_id': str(to_user_id),
                'to_username': to_username,
                'content': content,
                'timestamp': now_iso,
                'read': False,
                'delivered': False
            }
            
            print(f"💾 Message saved to Firestore: {message_id}")
            
            # Emit to sender (for immediate feedback)
            emit('dm_message', socket_message, room=request.sid)
            
            # Emit to recipient if online
            recipient_info = online_users.get(str(to_user_id))
            if recipient_info and recipient_info.get('sids'):
                for recipient_sid in recipient_info['sids']:
                    emit('dm_message', socket_message, room=recipient_sid)
                
                # Mark as delivered
                doc_ref[1].update({'delivered': True})
                socket_message['delivered'] = True
                
                print(f"📨 Message delivered to online user: {to_username}")
            else:
                print(f"📭 Recipient offline - message saved for later: {to_username}")
            
            # Broadcast to conversation room
            emit('dm_message', socket_message, room=conv_id, skip_sid=request.sid)
            
        except Exception as db_error:
            print(f"❌ Database error saving DM: {db_error}")
            import traceback
            traceback.print_exc()
            emit('error', {'message': 'Failed to save message'})
            
    except Exception as e:
        print(f"❌ Error sending DM: {e}")
        import traceback
        traceback.print_exc()
        emit('error', {'message': 'Failed to send message'})

# ============================================
# CLEANUP & MAINTENANCE ROUTES
# ============================================

@app.route('/api/chat/cleanup-old-messages', methods=['POST'])
def cleanup_old_messages():
    """
    Admin route to clean up messages older than X days
    This helps manage storage while keeping recent history
    """
    if 'user_id' not in session or session.get('role') != 'admin':
        return jsonify({'success': False, 'message': 'Admin access required'}), 403
    
    try:
        days = request.json.get('days', 90)  # Default: keep 90 days
        cutoff_date = datetime.now(timezone.utc) - timedelta(days=days)
        
        messages_ref = db.collection('direct_messages').where('timestamp', '<', cutoff_date)
        
        batch = db.batch()
        count = 0
        
        for doc in messages_ref.stream():
            batch.delete(doc.reference)
            count += 1
            
            # Firestore batch limit is 500
            if count >= 500:
                batch.commit()
                batch = db.batch()
        
        if count > 0:
            batch.commit()
        
        return jsonify({
            'success': True,
            'message': f'Cleaned up {count} old messages',
            'deleted': count
        })
        
    except Exception as e:
        print(f"Error cleaning up messages: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

@app.route('/api/chat/export-history', methods=['POST'])
def export_chat_history():
    """
    Export chat history for a conversation
    Useful for backup or analysis
    """
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        peer_id = data.get('peer_id')
        
        if not peer_id:
            return jsonify({'success': False, 'message': 'peer_id required'}), 400
        
        conversation_id = get_conversation_id(str(session['user_id']), str(peer_id))
        
        # Get all messages
        messages_ref = db.collection('direct_messages')\
            .where('conversation_id', '==', conversation_id)\
            .order_by('timestamp')
        
        messages = []
        for doc in messages_ref.stream():
            msg_data = doc.to_dict()
            messages.append({
                'from': msg_data.get('from_username'),
                'to': msg_data.get('to_username'),
                'content': msg_data.get('content'),
                'timestamp': msg_data.get('timestamp').isoformat() if hasattr(msg_data.get('timestamp'), 'isoformat') else str(msg_data.get('timestamp'))
            })
        
        return jsonify({
            'success': True,
            'conversation_id': conversation_id,
            'message_count': len(messages),
            'messages': messages,
            'exported_at': datetime.now().isoformat()
        })
        
    except Exception as e:
        print(f"Error exporting chat history: {e}")
        return jsonify({'success': False, 'message': str(e)}), 500

# Add this new route for searching users in chat
@app.route('/api/chat/search-users')
def search_chat_users():
    """Search for users to chat with (online or offline)"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    try:
        query = request.args.get('q', '').strip().lower()
        current_user_id = str(session['user_id'])
        
        if not query or len(query) < 2:
            return jsonify({'success': True, 'users': []})
        
        # Get all users from Firestore
        users_ref = db.collection('users')
        all_users = list(users_ref.stream())
        
        # Filter users based on search query
        matching_users = []
        for user_doc in all_users:
            user_data = user_doc.to_dict()
            user_id = user_doc.id
            
            # Skip current user
            if user_id == current_user_id:
                continue
            
            # Search in username, full_name, email
            username = user_data.get('username', '').lower()
            full_name = user_data.get('full_name', '').lower()
            email = user_data.get('email', '').lower()
            
            if query in username or query in full_name or query in email:
                # Check if user is currently online
                is_online = user_id in online_users
                
                # Get last message if exists
                conversation_id = get_conversation_id(current_user_id, user_id)
                last_message = None
                
                try:
                    last_msg_query = db.collection('direct_messages')\
                        .where('conversation_id', '==', conversation_id)\
                        .order_by('timestamp', direction=firestore.Query.DESCENDING)\
                        .limit(1)
                    
                    last_msg_docs = list(last_msg_query.stream())
                    if last_msg_docs:
                        last_msg_data = last_msg_docs[0].to_dict()
                        last_message = {
                            'content': last_msg_data.get('content', ''),
                            'timestamp': last_msg_data.get('timestamp'),
                            'from_user_id': last_msg_data.get('from_user_id')
                        }
                except Exception as e:
                    print(f"Error getting last message: {e}")
                
                matching_users.append({
                    'id': user_id,
                    'username': user_data.get('username', ''),
                    'full_name': user_data.get('full_name', ''),
                    'role': user_data.get('role', ''),
                    'avatar_type': user_data.get('avatar_type', 'initial'),
                    'avatar_id': user_data.get('avatar_id', 'blue'),
                    'is_online': is_online,
                    'last_message': last_message
                })
        
        # Sort by online status first, then by username
        matching_users.sort(key=lambda x: (not x['is_online'], x['username'].lower()))
        
        return jsonify({
            'success': True,
            'users': matching_users[:20]  # Limit to 20 results
        })
        
    except Exception as e:
        print(f"Error searching chat users: {e}")
        import traceback
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500



    
@app.route('/topic/<topic_id>/pdf')
def view_pdf(topic_id):
    """Serve PDF file for viewing"""
    try:
        print(f"\n{'='*60}")
        print(f"📄 PDF VIEW REQUEST")
        print(f"{'='*60}")
        print(f"Topic ID: {topic_id}")
        
        # Get topic
        topic_ref = db.collection('topics').document(topic_id)
        topic_doc = topic_ref.get()
        
        if not topic_doc.exists:
            print(f"❌ Topic not found")
            return "Topic not found", 404
        
        topic_data = topic_doc.to_dict()
        
        # Check access
        if 'user_id' not in session:
            print(f"❌ User not logged in")
            return "Please log in to view this PDF", 403
        
        # Verify permissions
        if session.get('role') == 'student':
            if not check_enrollment(session['user_id'], topic_data['subject_id']):
                print(f"❌ Student not enrolled")
                return "Access denied. Please enroll in this subject first.", 403
        elif session.get('role') == 'teacher':
            if topic_data.get('teacher_id') != session['user_id']:
                print(f"❌ Teacher doesn't own this topic")
                return "Access denied", 403
        
        # Get PDF URL
        pdf_url = topic_data.get('pdf_url', '')
        print(f"PDF URL from DB: '{pdf_url}'")
        
        if not pdf_url:
            print(f"❌ No PDF URL in database")
            return "No PDF file found for this topic", 404
        
        # ✅ EXTRACT FILENAME - Handle all URL formats
        if '/static/uploads/pdfs/topics/' in pdf_url:
            filename = pdf_url.split('/static/uploads/pdfs/topics/')[-1]
        elif 'static/uploads/pdfs/topics/' in pdf_url:
            filename = pdf_url.split('static/uploads/pdfs/topics/')[-1]
        else:
            filename = os.path.basename(pdf_url)
        
        # ✅ Build ABSOLUTE file path using os.path.join (handles Windows/Linux)
        full_path = os.path.join(UPLOAD_FOLDER, filename)
        
        # ✅ Normalize path for Windows (convert forward slashes to backslashes)
        full_path = os.path.normpath(full_path)
        
        print(f"Extracted filename: '{filename}'")
        print(f"Upload folder: '{UPLOAD_FOLDER}'")
        print(f"Full path: '{full_path}'")
        print(f"File exists: {os.path.exists(full_path)}")
        
        # FINAL CHECK
        if not os.path.exists(full_path):
            print(f"\n❌ FILE NOT FOUND!")
            print(f"Checked path: {full_path}")
            
            # List directory contents for debugging
            if os.path.exists(UPLOAD_FOLDER):
                files = os.listdir(UPLOAD_FOLDER)
                print(f"\nFiles in upload folder ({len(files)} total):")
                for f in files:
                    print(f"   - {f}")
                    # Try direct match
                    if f == filename:
                        print(f"   ✅ FOUND EXACT MATCH!")
                        full_path = os.path.join(UPLOAD_FOLDER, f)
                        break
            else:
                print(f"Upload folder doesn't exist: {UPLOAD_FOLDER}")
            
            # If still not found after checking
            if not os.path.exists(full_path):
                return f"PDF file not found. Please contact your teacher.", 404
        
        print(f"✅ PDF file found, serving from: {full_path}")
        print(f"{'='*60}\n")
        
        # ✅ Serve the file with proper error handling
        try:
            return send_file(
                full_path,
                mimetype='application/pdf',
                as_attachment=False,
                download_name=topic_data.get('pdf_filename', 'document.pdf')
            )
        except Exception as send_error:
            print(f"❌ Error sending file: {send_error}")
            traceback.print_exc()
            return f"Error serving PDF file: {str(send_error)}", 500
        
    except Exception as e:
        print(f"\n❌ ERROR serving PDF:")
        print(f"Error: {e}")
        traceback.print_exc()
        return f"Error loading PDF: {str(e)}", 500

@app.route('/topic/<topic_id>/pdf/download')
def download_pdf(topic_id):
    """Download PDF file"""
    try:
        # Get topic to verify access and get PDF path
        topic_ref = db.collection('topics').document(topic_id)
        topic_doc = topic_ref.get()
        
        if not topic_doc.exists:
            return "Topic not found", 404
        
        topic_data = topic_doc.to_dict()
        
        # Check if user has access
        if 'user_id' not in session:
            return "Please log in to download this PDF", 403
        
        # Verify access
        if session.get('role') == 'student':
            if not check_enrollment(session['user_id'], topic_data['subject_id']):
                return "Access denied. Please enroll in this subject first.", 403
        elif session.get('role') == 'teacher':
            if topic_data.get('teacher_id') != session['user_id']:
                return "Access denied", 403
        
        # Get PDF URL
        pdf_url = topic_data.get('pdf_url', '')
        if not pdf_url:
            return "No PDF file found for this topic", 404
        
        # Convert URL to file path
        pdf_path = pdf_url.replace('/static/', '').replace('static/', '')
        full_path = os.path.join(app.static_folder, pdf_path)
        
        # Check if file exists
        if not os.path.exists(full_path):
            return "PDF file not found", 404
        
        # Serve the PDF file as download
        return send_file(
            full_path,
            mimetype='application/pdf',
            as_attachment=True,  # Force download
            download_name=topic_data.get('pdf_filename', 'document.pdf')
        )
        
    except Exception as e:
        print(f"❌ Error downloading PDF: {e}")
        traceback.print_exc()
        return f"Error downloading PDF: {str(e)}", 500

@app.route('/topic/<topic_id>/debug-pdf')
def debug_pdf_info(topic_id):
    """Debug route to check PDF storage - REMOVE IN PRODUCTION"""
    if 'user_id' not in session or session.get('role') != 'teacher':
        return "Access denied", 403
    
    try:
        topic_ref = db.collection('topics').document(topic_id)
        topic_doc = topic_ref.get()
        
        if not topic_doc.exists:
            return "Topic not found", 404
        
        topic_data = topic_doc.to_dict()
        
        debug_info = {
            'topic_id': topic_id,
            'pdf_url_in_db': topic_data.get('pdf_url', 'NOT SET'),
            'pdf_filename': topic_data.get('pdf_filename', 'NOT SET'),
            'upload_folder': UPLOAD_FOLDER,
            'static_folder': app.static_folder,
        }
        
        # Check if PDF URL exists
        pdf_url = topic_data.get('pdf_url', '')
        if pdf_url:
            # Try to extract filename and check different paths
            possible_paths = []
            
            # Path 1: Direct from URL
            if '/static/uploads/pdfs/topics/' in pdf_url:
                filename = pdf_url.split('/static/uploads/pdfs/topics/')[-1]
                path1 = os.path.join(UPLOAD_FOLDER, filename)
                possible_paths.append(('Extracted from URL', path1, os.path.exists(path1)))
            
            # Path 2: Just the filename
            filename = os.path.basename(pdf_url)
            path2 = os.path.join(UPLOAD_FOLDER, filename)
            possible_paths.append(('Basename only', path2, os.path.exists(path2)))
            
            # Path 3: Full path from static
            path3 = os.path.join(app.static_folder, pdf_url.lstrip('/').replace('static/', ''))
            possible_paths.append(('Full path from static', path3, os.path.exists(path3)))
            
            debug_info['possible_paths'] = possible_paths
        
        # List all files in upload folder
        if os.path.exists(UPLOAD_FOLDER):
            all_files = os.listdir(UPLOAD_FOLDER)
            debug_info['files_in_upload_folder'] = all_files[:10]  # First 10 files
            debug_info['total_files'] = len(all_files)
        
        import json
        return f"<html><body><pre>{json.dumps(debug_info, indent=2)}</pre></body></html>"
        
    except Exception as e:
        import traceback
        return f"<pre>Error: {str(e)}\n\n{traceback.format_exc()}</pre>"
    
@app.route('/aboutus')
def about():
    username = session.get('username')
    role = session.get('role', 'guest')
    # pass any other context your base.html expects (e.g. unread_notifications_count)
    return render_template('aboutus.html', username=username, role=role)

@app.route('/contact')
def contact():
    """Contact Us page"""
    username = session.get('username')
    role = session.get('role', 'guest')
    return render_template('contactus.html', username=username, role=role)

@app.route('/terms')
def terms():
    """Terms of Service page"""
    username = session.get('username')
    role = session.get('role', 'guest')
    return render_template('terms.html', username=username, role=role)

@app.route('/privacy')
def privacy():
    """Privacy Policy page"""
    username = session.get('username')
    role = session.get('role', 'guest')
    return render_template('privacy.html', username=username, role=role)

@app.route('/help')
def help_center():
    """Help Center page with FAQs and support information"""
    return render_template('helpcenter.html',
                         username=session.get('username'),
                         role=session.get('role'))

@app.route('/subject/<subject_id>/create-coding-exercise', methods=['GET', 'POST'])
def create_coding_exercise(subject_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        flash('Access denied. Teachers only.')
        return redirect(url_for('dashboard'))
    
    try:
        # Verify the subject exists and belongs to the teacher
        subject_ref = db.collection('subjects').document(subject_id)
        subject_doc = subject_ref.get()
        
        if not subject_doc.exists or subject_doc.to_dict()['teacher_id'] != session['user_id']:
            flash('Subject not found or access denied.')
            return redirect(url_for('dashboard'))
        
        subject_data = subject_doc.to_dict()
        subject_data['id'] = subject_doc.id
        
        if request.method == 'POST':
            title = request.form.get('title', '').strip()
            description = request.form.get('description', '').strip()
            language = request.form.get('language', 'python')
            difficulty = request.form.get('difficulty', 'beginner')
            starter_code = request.form.get('starter_code', '').strip()
            solution_code = request.form.get('solution_code', '').strip()
            test_cases = request.form.get('test_cases', '').strip()
            hints = request.form.get('hints', '').strip()
            
            if not title or not description:
                flash('Title and description are required.')
                return redirect(request.url)
            
            # Create the coding exercise document
            exercise_data = {
                'title': title,
                'description': description,
                'language': language,
                'difficulty': difficulty,
                'starter_code': starter_code,
                'solution_code': solution_code,
                'test_cases': test_cases,
                'hints': hints.split('\n') if hints else [],
                'subject_id': subject_id,
                'teacher_id': session['user_id'],
                'is_published': False,  # Draft by default
                'created_at': datetime.now(),
                'updated_at': datetime.now()
            }
            
            # Add to Firestore
            exercise_ref = db.collection('coding_exercises').add(exercise_data)
            exercise_id = exercise_ref[1].id
            
            flash(f'Coding exercise "{title}" created successfully!')
            return redirect(url_for('view_coding_exercise', exercise_id=exercise_id))
        
        return render_template('create_coding_exercise.html',
                             subject=subject_data,
                             username=session.get('username'),
                             role=session.get('role'))
    
    except Exception as e:
        print(f"Error creating coding exercise: {e}")
        flash('Error creating coding exercise.')
        return redirect(url_for('dashboard'))
# Find and replace the view_coding_exercise route (around line 5330)

@app.route('/coding-exercise/<exercise_id>')
def view_coding_exercise(exercise_id):
    if 'user_id' not in session:
        flash('Please log in to view this exercise.')
        return redirect(url_for('login'))
    
    try:
        exercise_ref = db.collection('coding_exercises').document(exercise_id)
        exercise_doc = exercise_ref.get()
        
        if not exercise_doc.exists:
            flash('Exercise not found.')
            return redirect(url_for('dashboard'))
        
        exercise_data = exercise_doc.to_dict()
        exercise_data['id'] = exercise_doc.id
        
        # Check permissions
        is_teacher = session.get('role') == 'teacher' and exercise_data.get('teacher_id') == session['user_id']
        is_student = session.get('role') == 'student' and exercise_data.get('is_published', False)
        
        if not (is_teacher or is_student):
            flash('You do not have permission to view this exercise.')
            return redirect(url_for('dashboard'))
        
        # Get subject info
        subject_ref = db.collection('subjects').document(exercise_data['subject_id'])
        subject_doc = subject_ref.get()
        subject_data = subject_doc.to_dict() if subject_doc.exists else {}
        exercise_data['subject_name'] = subject_data.get('name', 'Unknown Subject')
        
        # For students, get their previous attempts WITHOUT ordering
        # (to avoid needing a Firestore index)
        attempts = []
        if session.get('role') == 'student':
            # Remove the order_by to avoid index requirement
            attempts_ref = db.collection('coding_attempts')\
                .where('exercise_id', '==', exercise_id)\
                .where('student_id', '==', session['user_id'])
            
            # Get all attempts
            attempts_list = []
            for doc in attempts_ref.stream():
                attempt_data = doc.to_dict()
                attempt_data['id'] = doc.id
                attempts_list.append(attempt_data)
            
            # Sort in Python instead of Firestore
            attempts = sorted(attempts_list, 
                            key=lambda x: x.get('submitted_at', datetime.min), 
                            reverse=True)[:5]  # Get last 5 attempts
        
        return render_template('view_coding_exercise.html',
                             exercise=exercise_data,
                             is_teacher=is_teacher,
                             attempts=attempts,
                             username=session.get('username'),
                             role=session.get('role'))
    
    except Exception as e:
        print(f"Error viewing coding exercise: {e}")
        import traceback
        traceback.print_exc()
        flash('Error loading exercise.')
        return redirect(url_for('dashboard'))
@app.route('/coding-exercise/<exercise_id>/publish', methods=['POST'])
def publish_coding_exercise(exercise_id):
    if 'user_id' not in session or session.get('role') != 'teacher':
        return jsonify({'success': False}), 403
    
    try:
        exercise_ref = db.collection('coding_exercises').document(exercise_id)
        exercise_doc = exercise_ref.get()
        
        if not exercise_doc.exists or exercise_doc.to_dict()['teacher_id'] != session['user_id']:
            return jsonify({'success': False}), 404
        
        exercise_ref.update({'is_published': True})
        
        return jsonify({'success': True})
    except Exception as e:
        print(f"Error publishing exercise: {e}")
        return jsonify({'success': False}), 500

@app.route('/coding-exercise/<exercise_id>/submit', methods=['POST'])
def submit_coding_exercise(exercise_id):
    if 'user_id' not in session or session.get('role') != 'student':
        return jsonify({'success': False, 'error': 'Not authorized'}), 403
    
    try:
        data = request.get_json()
        code = data.get('code', '')
        
        if not code:
            return jsonify({'success': False, 'error': 'No code provided'}), 400
        
        # Get the exercise
        exercise_ref = db.collection('coding_exercises').document(exercise_id)
        exercise_doc = exercise_ref.get()
        
        if not exercise_doc.exists:
            return jsonify({'success': False, 'error': 'Exercise not found'}), 404
        
        exercise_data = exercise_doc.to_dict()
        
        # Parse and run test cases (simplified version)
        test_cases = exercise_data.get('test_cases', '').split('\n')
        results = []
        passed = 0
        total = len([tc for tc in test_cases if tc.strip()])
        
        for test_case in test_cases:
            if not test_case.strip():
                continue
            
            try:
                input_val, expected = test_case.split('|')
                # Note: In production, you'd want to use a sandboxed environment
                # This is a simplified example
                results.append({
                    'input': input_val,
                    'expected': expected,
                    'passed': True,  # Simplified - actual execution needed
                    'output': expected
                })
                passed += 1
            except Exception as e:
                results.append({
                    'input': input_val if 'input_val' in locals() else '',
                    'expected': expected if 'expected' in locals() else '',
                    'passed': False,
                    'output': str(e)
                })
        
        # Store the attempt
        attempt_data = {
            'exercise_id': exercise_id,
            'student_id': session['user_id'],
            'code': code,
            'passed': passed,
            'total': total,
            'results': results,
            'submitted_at': datetime.now()
        }
        
        db.collection('coding_attempts').add(attempt_data)
        
        return jsonify({
            'success': True,
            'passed': passed,
            'total': total,
            'results': results
        })
    
    except Exception as e:
        print(f"Error submitting code: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

# ...existing code...
@app.route('/api/run-code', methods=['POST'])
def run_code():
    """Execute code safely and return results"""
    if 'user_id' not in session:
        return jsonify({'success': False, 'error': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        code = data.get('code', '')
        language = data.get('language', 'python')
        test_input = data.get('input', '')
        
        if not code:
            return jsonify({'success': False, 'error': 'No code provided'}), 400
        
        # Execute code based on language
        if language == 'python':
            result = execute_python_code(code, test_input)
        elif language == 'javascript':
            result = execute_javascript_code(code, test_input)
        else:
            return jsonify({'success': False, 'error': f'Language {language} not supported'}), 400
        
        return jsonify({
            'success': True,
            'output': result['output'],
            'error': result.get('error'),
            'execution_time': result.get('execution_time')
        })
    
    except Exception as e:
        print(f"Error running code: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500

def execute_python_code(code, test_input=''):
    """Safely execute Python code with timeout"""
    import sys
    from io import StringIO
    import time
    
    # Capture stdout
    old_stdout = sys.stdout
    redirected_output = sys.stdout = StringIO()
    
    start_time = time.time()
    error = None
    
    try:
        # Create restricted globals
        restricted_globals = {
            '__builtins__': {
                'print': print,
                'len': len,
                'range': range,
                'str': str,
                'int': int,
                'float': float,
                'list': list,
                'dict': dict,
                'tuple': tuple,
                'set': set,
                'abs': abs,
                'max': max,
                'min': min,
                'sum': sum,
                'sorted': sorted,
                'enumerate': enumerate,
                'zip': zip,
            }
        }
        
        # Add input if provided
        if test_input:
            restricted_globals['input'] = lambda: test_input
        
        # Execute with timeout (5 seconds)
        exec(code, restricted_globals)
        
    except Exception as e:
        error = str(e)
    finally:
        sys.stdout = old_stdout
    
    execution_time = time.time() - start_time
    output = redirected_output.getvalue()
    
    return {
        'output': output,
        'error': error,
        'execution_time': round(execution_time, 3)
    }

def execute_javascript_code(code, test_input=''):
    """Execute JavaScript code using Node.js"""
    import tempfile
    
    try:
        # Create temporary file
        with tempfile.NamedTemporaryFile(mode='w', suffix='.js', delete=False) as f:
            f.write(code)
            temp_file = f.name
        
        # Run Node.js
        result = subprocess.run(
            ['node', temp_file],
            input=test_input,
            capture_output=True,
            text=True,
            timeout=5
        )
        
        # Clean up
        os.unlink(temp_file)
        
        return {
            'output': result.stdout,
            'error': result.stderr if result.returncode != 0 else None,
            'execution_time': 0
        }
    
    except subprocess.TimeoutExpired:
        return {'output': '', 'error': 'Execution timeout (5 seconds)', 'execution_time': 5}
    except Exception as e:
        return {'output': '', 'error': str(e), 'execution_time': 0}

@app.route('/api/teacher/subjects')
def get_teacher_subjects():
    """Get all subjects created by the logged-in teacher"""
    if 'user_id' not in session or session.get('role') != 'teacher':
        return jsonify({'success': False, 'error': 'Not authorized'}), 403
    
    try:
        subjects_ref = db.collection('subjects').where('teacher_id', '==', session['user_id'])
        
        subjects = []
        for doc in subjects_ref.stream():
            subject_data = doc.to_dict()
            subject_data['id'] = doc.id
            
            # Count topics
            topics_ref = db.collection('topics').where('subject_id', '==', doc.id)
            subject_data['topic_count'] = len(list(topics_ref.stream()))
            
            # Format date
            if subject_data.get('created_at'):
                subject_data['created_at'] = subject_data['created_at'].strftime('%b %Y')
            
            subjects.append(subject_data)
        
        # Sort by creation date (newest first)
        subjects.sort(key=lambda x: x.get('created_at', ''), reverse=True)
        
        return jsonify({
            'success': True,
            'subjects': subjects
        })
    except Exception as e:
        print(f"Error fetching teacher subjects: {e}")
        return jsonify({'success': False, 'error': str(e)}), 500
# ...existing code...
# Find and replace the view_all_coding_exercises route

@app.route('/coding-exercises')
def view_all_coding_exercises():
    """View all coding exercises - students see published, teachers see their own"""
    if 'user_id' not in session:
        flash('Please log in to view coding exercises.')
        return redirect(url_for('login'))
    
    try:
        exercises = []
        
        if session.get('role') == 'teacher':
            # Teachers see only their own exercises - NO ORDER BY
            exercises_ref = db.collection('coding_exercises')\
                .where('teacher_id', '==', session['user_id'])
        else:
            # Students see only published exercises - NO ORDER BY
            exercises_ref = db.collection('coding_exercises')\
                .where('is_published', '==', True)
        
        # Get all exercises
        exercises_list = []
        for doc in exercises_ref.stream():
            exercise_data = doc.to_dict()
            exercise_data['id'] = doc.id
            
            # Get subject name
            subject_ref = db.collection('subjects').document(exercise_data.get('subject_id', ''))
            subject_doc = subject_ref.get()
            if subject_doc.exists:
                exercise_data['subject_name'] = subject_doc.to_dict().get('name', 'Unknown')
            else:
                exercise_data['subject_name'] = 'Unknown'
            
            # For students, get attempt count
            if session.get('role') == 'student':
                attempts_ref = db.collection('coding_attempts')\
                    .where('exercise_id', '==', doc.id)\
                    .where('student_id', '==', session['user_id'])
                
                attempts_list = list(attempts_ref.stream())
                exercise_data['attempt_count'] = len(attempts_list)
                
                # Get best score
                best_score = 0
                for attempt_doc in attempts_list:
                    attempt = attempt_doc.to_dict()
                    total = attempt.get('total', 0)
                    if total > 0:
                        score = (attempt.get('passed', 0) / total) * 100
                        best_score = max(best_score, score)
                
                exercise_data['best_score'] = best_score
            
            exercises_list.append(exercise_data)
        
        # Sort by created_at in Python (newest first)
        exercises = sorted(exercises_list, 
                         key=lambda x: x.get('created_at', datetime.min), 
                         reverse=True)
        
        return render_template('all_coding_exercises.html',
                             exercises=exercises,
                             username=session.get('username'),
                             role=session.get('role'))
    
    except Exception as e:
        print(f"Error loading coding exercises: {e}")
        import traceback
        traceback.print_exc()
        flash('Error loading coding exercises.')
        return redirect(url_for('dashboard'))
    # Add these routes after your existing coding exercise routes

@app.route('/coding-exercise/<exercise_id>/unpublish', methods=['POST'])
def unpublish_coding_exercise(exercise_id):
    """Unpublish a coding exercise"""
    if 'user_id' not in session or session.get('role') != 'teacher':
        return jsonify({'success': False, 'message': 'Not authorized'}), 403
    
    try:
        exercise_ref = db.collection('coding_exercises').document(exercise_id)
        exercise_doc = exercise_ref.get()
        
        if not exercise_doc.exists:
            return jsonify({'success': False, 'message': 'Exercise not found'}), 404
        
        exercise_data = exercise_doc.to_dict()
        
        # Verify ownership
        if exercise_data.get('teacher_id') != session['user_id']:
            return jsonify({'success': False, 'message': 'Not authorized'}), 403
        
        # Unpublish the exercise
        exercise_ref.update({
            'is_published': False,
            'updated_at': datetime.now()
        })
        
        print(f"✅ Exercise unpublished: {exercise_id}")
        return jsonify({'success': True, 'message': 'Exercise unpublished successfully'})
        
    except Exception as e:
        print(f"❌ Error unpublishing exercise: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': 'Error unpublishing exercise'}), 500


@app.route('/coding-exercise/<exercise_id>/delete', methods=['POST'])
def delete_coding_exercise(exercise_id):
    """Delete a coding exercise"""
    if 'user_id' not in session or session.get('role') != 'teacher':
        return jsonify({'success': False, 'message': 'Not authorized'}), 403
    
    try:
        exercise_ref = db.collection('coding_exercises').document(exercise_id)
        exercise_doc = exercise_ref.get()
        
        if not exercise_doc.exists:
            return jsonify({'success': False, 'message': 'Exercise not found'}), 404
        
        exercise_data = exercise_doc.to_dict()
        
        # Verify ownership
        if exercise_data.get('teacher_id') != session['user_id']:
            return jsonify({'success': False, 'message': 'Not authorized'}), 403
        
        subject_id = exercise_data.get('subject_id')
        
        # Delete all attempts for this exercise
        attempts_ref = db.collection('coding_attempts').where('exercise_id', '==', exercise_id)
        for attempt_doc in attempts_ref.stream():
            attempt_doc.reference.delete()
        
        # Delete the exercise
        exercise_ref.delete()
        
        print(f"✅ Exercise deleted: {exercise_id}")
        return jsonify({
            'success': True, 
            'message': 'Exercise deleted successfully',
            'subject_id': subject_id
        })
        
    except Exception as e:
        print(f"❌ Error deleting exercise: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': 'Error deleting exercise'}), 500


@app.route('/coding-exercise/<exercise_id>/edit', methods=['GET', 'POST'])
def edit_coding_exercise(exercise_id):
    """Edit a coding exercise"""
    if 'user_id' not in session or session.get('role') != 'teacher':
        flash('Access denied. Teachers only.')
        return redirect(url_for('dashboard'))
    
    try:
        exercise_ref = db.collection('coding_exercises').document(exercise_id)
        exercise_doc = exercise_ref.get()
        
        if not exercise_doc.exists:
            flash('Exercise not found.')
            return redirect(url_for('view_all_coding_exercises'))
        
        exercise_data = exercise_doc.to_dict()
        exercise_data['id'] = exercise_doc.id
        
        # Verify ownership
        if exercise_data.get('teacher_id') != session['user_id']:
            flash('Access denied.')
            return redirect(url_for('view_all_coding_exercises'))
        
        # Get subject info
        subject_ref = db.collection('subjects').document(exercise_data['subject_id'])
        subject_doc = subject_ref.get()
        subject_data = subject_doc.to_dict() if subject_doc.exists else {}
        
        if request.method == 'POST':
            title = request.form.get('title', '').strip()
            description = request.form.get('description', '').strip()
            language = request.form.get('language', 'python')
            difficulty = request.form.get('difficulty', 'beginner')
            starter_code = request.form.get('starter_code', '').strip()
            solution_code = request.form.get('solution_code', '').strip()
            test_cases = request.form.get('test_cases', '').strip()
            hints = request.form.get('hints', '').strip()
            
            if not title or not description:
                flash('Title and description are required.')
                return redirect(request.url)
            
            # Update the exercise
            update_data = {
                'title': title,
                'description': description,
                'language': language,
                'difficulty': difficulty,
                'starter_code': starter_code,
                'solution_code': solution_code,
                'test_cases': test_cases,
                'hints': hints.split('\n') if hints else [],
                'updated_at': datetime.now()
            }
            
            exercise_ref.update(update_data)
            
            flash('Exercise updated successfully!')
            return redirect(url_for('view_coding_exercise', exercise_id=exercise_id))
        
        # Convert hints list to string for textarea
        if isinstance(exercise_data.get('hints'), list):
            exercise_data['hints_text'] = '\n'.join(exercise_data['hints'])
        else:
            exercise_data['hints_text'] = exercise_data.get('hints', '')
        
        return render_template('edit_coding_exercise.html',
                             exercise=exercise_data,
                             subject=subject_data,
                             username=session.get('username'),
                             role=session.get('role'))
        
    except Exception as e:
        print(f"❌ Error editing exercise: {e}")
        traceback.print_exc()
        flash('Error loading exercise.')
        return redirect(url_for('view_all_coding_exercises'))

# ==================== AI FLASHCARDS ROUTES ====================

@app.route('/ai-flashcards/<topic_id>')
def ai_flashcards(topic_id):
    """Display AI flashcards generation page"""
    if 'user_id' not in session:
        flash('Please log in to access flashcards.', 'error')
        return redirect(url_for('login'))
    
    try:
        # Get topic details
        topic_ref = db.collection('topics').document(topic_id)
        topic_doc = topic_ref.get()
        
        if not topic_doc.exists:
            flash('Topic not found.', 'error')
            return redirect(url_for('dashboard'))
        
        topic_data = topic_doc.to_dict()
        
        # Check access for students
        if session.get('role') == 'student':
            if not check_enrollment(session['user_id'], topic_data['subject_id']):
                flash('You need to enroll in this subject to access flashcards.', 'error')
                return redirect(url_for('view_subject', subject_id=topic_data['subject_id']))
        
        return render_template('ai_flashcards.html',
                             topic_id=topic_id,
                             topic_title=topic_data.get('title', 'Topic'),
                             username=session.get('username'),
                             role=session.get('role'))
    
    except Exception as e:
        print(f"Error loading flashcards page: {e}")
        flash('Error loading flashcards page.', 'error')
        return redirect(url_for('dashboard'))

@app.route('/api/generate-flashcards', methods=['POST'])
def generate_flashcards_api():
    """API endpoint to generate flashcards from topic content"""
    if 'user_id' not in session:
        print("Error: User not authenticated")
        return jsonify({'success': False, 'message': 'Not authenticated'}), 401
    
    try:
        data = request.get_json()
        print(f"Received flashcard generation request: {data}")
        
        topic_id = data.get('topic_id')
        count = data.get('count', 10)
        difficulty = data.get('difficulty', 'medium')
        
        print(f"Topic ID: {topic_id}, Count: {count}, Difficulty: {difficulty}")
        
        # Get topic content
        topic_ref = db.collection('topics').document(topic_id)
        topic_doc = topic_ref.get()
        
        if not topic_doc.exists:
            print(f"Error: Topic {topic_id} not found")
            return jsonify({'success': False, 'message': 'Topic not found'}), 404
        
        topic_data = topic_doc.to_dict()
        print(f"Topic data retrieved: {topic_data.get('title')}")
        
        # Check access
        if session.get('role') == 'student':
            if not check_enrollment(session['user_id'], topic_data['subject_id']):
                print(f"Error: Student not enrolled in subject")
                return jsonify({'success': False, 'message': 'Access denied'}), 403
        
        # Extract content for flashcard generation
        content_text = topic_data.get('content_text', '')
        title = topic_data.get('title', 'Topic')
        
        if not content_text:
            print(f"Error: No content available for topic {topic_id}")
            return jsonify({'success': False, 'message': 'No content available for flashcard generation'}), 400
        
        # Generate flashcards (mock implementation - replace with actual AI later)
        flashcards = generate_flashcards_from_content(title, content_text, count, difficulty)
        print(f"Generated {len(flashcards)} flashcards successfully")
        
        return jsonify({
            'success': True,
            'flashcards': flashcards
        })
    
    except Exception as e:
        print(f"Error generating flashcards: {e}")
        traceback.print_exc()
        return jsonify({'success': False, 'message': str(e)}), 500

def generate_flashcards_from_content(title, content, count, difficulty):
    """Generate flashcards from content (mock implementation)"""
    # This is a simple mock - in production, you'd use an AI API like OpenAI
    templates = {
        'easy': [
            {'q': f'What is the main topic of "{title}"?', 'a': f'The main topic is {title}, which covers fundamental concepts and principles.'},
            {'q': f'Why is "{title}" important?', 'a': f'{title} is important because it provides foundational knowledge for further learning.'},
            {'q': f'What are the key components of "{title}"?', 'a': 'The key components include the main concepts, definitions, and practical applications.'},
            {'q': f'How does "{title}" apply in real-world scenarios?', 'a': 'It applies through various practical examples and use cases.'},
            {'q': f'What should you remember most about "{title}"?', 'a': 'The most important aspects are the core principles and their applications.'}
        ],
        'medium': [
            {'q': f'How does "{title}" relate to other concepts?', 'a': f'{title} connects to other concepts by building upon previous knowledge.'},
            {'q': f'What are the practical applications of "{title}"?', 'a': 'The practical applications include real-world examples and use cases.'},
            {'q': f'What challenges might arise when learning "{title}"?', 'a': 'Common challenges include understanding complex terminology and abstract concepts.'},
            {'q': f'Compare and contrast key aspects of "{title}"', 'a': 'Different aspects have unique characteristics that complement each other.'},
            {'q': f'What are the prerequisites for understanding "{title}"?', 'a': 'You should have a basic understanding of related fundamental concepts.'}
        ],
        'hard': [
            {'q': f'Analyze the deeper implications of "{title}"', 'a': f'{title} has far-reaching implications in the broader context of the field.'},
            {'q': f'How would you evaluate the effectiveness of "{title}"?', 'a': 'Effectiveness can be measured through various metrics and outcomes.'},
            {'q': f'Synthesize the main concepts of "{title}"', 'a': 'The synthesis involves integrating multiple concepts into a cohesive understanding.'},
            {'q': f'What are the theoretical foundations of "{title}"?', 'a': 'The theoretical foundations are based on established principles and research.'},
            {'q': f'Critically assess the limitations of "{title}"', 'a': 'Limitations include specific constraints and areas requiring further development.'}
        ]
    }
    
    selected_templates = templates.get(difficulty, templates['medium'])
    flashcards = []
    
    # Generate requested number of flashcards
    for i in range(count):
        template = selected_templates[i % len(selected_templates)]
        flashcards.append({
            'question': template['q'],
            'answer': template['a'],
            'difficulty': difficulty
        })
    
    return flashcards

@app.route('/flashcard-quiz/<topic_id>')
def flashcard_quiz(topic_id):
    """Display flashcard quiz mode"""
    if 'user_id' not in session:
        flash('Please log in to access the quiz.', 'error')
        return redirect(url_for('login'))
    
    try:
        # Get topic details
        topic_ref = db.collection('topics').document(topic_id)
        topic_doc = topic_ref.get()
        
        if not topic_doc.exists:
            flash('Topic not found.', 'error')
            return redirect(url_for('dashboard'))
        
        topic_data = topic_doc.to_dict()
        
        # Check access for students
        if session.get('role') == 'student':
            if not check_enrollment(session['user_id'], topic_data['subject_id']):
                flash('You need to enroll in this subject to access the quiz.', 'error')
                return redirect(url_for('view_subject', subject_id=topic_data['subject_id']))
        
        return render_template('flashcard_quiz.html',
                             topic_id=topic_id,
                             topic_title=topic_data.get('title', 'Topic'),
                             username=session.get('username'),
                             role=session.get('role'))
    
    except Exception as e:
        print(f"Error loading quiz page: {e}")
        flash('Error loading quiz page.', 'error')
        return redirect(url_for('dashboard'))
    
if __name__ == '__main__':
    # app.run(debug=True)   
    socketio.run(app, debug=True, host='0.0.0.0', port=5000, allow_unsafe_werkzeug=True)
