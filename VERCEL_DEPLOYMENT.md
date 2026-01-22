# Vercel Deployment Guide for Quizera

## Prerequisites
- GitHub account with your repository
- Vercel account
- Firebase project with service account key

## Step 1: Prepare Your Repository
Your repository is already set up with:
- ✅ `.gitignore` (config.py and firebase-key.json are ignored)
- ✅ `vercel.json` (Vercel configuration)
- ✅ `api/index.py` (Entry point for Vercel)
- ✅ `requirements.txt` (Python dependencies)

## Step 2: Set Up Environment Variables in Vercel

After importing your project to Vercel, add these environment variables:

### Required Variables:

1. **SECRET_KEY**
   - Your Flask secret key
   - Example: `your-super-secret-key-12345`

2. **FIREBASE_CREDENTIALS**
   - The ENTIRE content of your `firebase-key.json` file
   - Copy the complete JSON content (including the curly braces)
   - Paste it as a single line or multiline text
   - Example: `{"type": "service_account", "project_id": "your-project", ...}`

3. **MAIL_USERNAME**
   - Your email address for sending emails
   - Example: `your-email@gmail.com`

4. **MAIL_PASSWORD**
   - Your email app password (not your regular password)
   - For Gmail: Generate an App Password in Google Account settings
   - Example: `abcd efgh ijkl mnop`

5. **FLASK_ENV** (Optional, already set in vercel.json)
   - Value: `production`

## Step 3: Deploy to Vercel

### Option A: Using Vercel CLI
```bash
# Install Vercel CLI
npm i -g vercel

# Login to Vercel
vercel login

# Deploy
vercel
```

### Option B: Using Vercel Dashboard
1. Go to https://vercel.com
2. Click "Add New Project"
3. Import your GitHub repository
4. Vercel will auto-detect the configuration
5. Add environment variables in Settings > Environment Variables
6. Deploy!

## Step 4: Important Notes

### Firebase Credentials
- Never commit `firebase-key.json` to GitHub
- Use the `FIREBASE_CREDENTIALS` environment variable in Vercel
- Copy the entire JSON content from your firebase-key.json file

### Email Configuration
- If using Gmail, you must use an App Password, not your regular password
- Enable 2FA on your Google account to generate App Passwords
- Go to: Google Account > Security > 2-Step Verification > App Passwords

### Static Files & Uploads
- Vercel's serverless functions have limited file system access
- Consider using cloud storage (Firebase Storage, AWS S3) for user uploads
- Your current uploads in `static/uploads/` won't persist across deployments

### WebSocket Support
- Vercel has limited WebSocket support
- Flask-SocketIO may not work properly on Vercel
- Consider using Vercel's Edge Functions or deploy to Railway/Render for full WebSocket support

## Troubleshooting

### Issue: "Firebase credentials not found"
- Make sure you've added `FIREBASE_CREDENTIALS` environment variable
- Verify the JSON is properly formatted
- Check that all quotes are properly escaped

### Issue: Email not sending
- Verify `MAIL_USERNAME` and `MAIL_PASSWORD` are set
- Use Gmail App Password, not regular password
- Check Gmail security settings

### Issue: Import errors
- Ensure all packages are in `requirements.txt`
- Check Python version compatibility
- Vercel uses Python 3.9 by default

## Alternative Deployment Platforms

If you encounter issues with Vercel (especially with WebSocket/SocketIO), consider:
- **Railway** - Better for WebSocket support
- **Render** - Full Python support with persistent storage
- **Heroku** - Traditional platform with good Flask support
- **Google Cloud Run** - Integrated with Firebase

## Getting Firebase Credentials JSON

To copy your Firebase credentials:
```bash
# On Windows
type firebase-key.json | clip

# Or open the file and copy manually
notepad firebase-key.json
```

Then paste the entire content into Vercel's `FIREBASE_CREDENTIALS` environment variable.
