import smtplib
from email.message import EmailMessage
import os
from jinja2 import Environment, FileSystemLoader

SMTP_HOST = os.getenv("SMTP_HOST", "127.0.0.1")
SMTP_PORT = int(os.getenv("SMTP_PORT", 1025))
SENDER_EMAIL = os.getenv("SENDER_EMAIL", "noreply@gbu.ac.in")

# Point Jinja2 to your new templates directory
template_env = Environment(loader=FileSystemLoader(searchpath="app/templates"))

def send_otp_email(to_email: str, otp: str):
    """Synchronous email dispatcher designed to run as a BackgroundTask."""
    msg = EmailMessage()
    msg["Subject"] = "Password Reset Request - GBU Attendance"
    msg["From"] = SENDER_EMAIL
    msg["To"] = to_email
    
    # 1. Provide a plain-text fallback (required for spam-filter compliance)
    fallback_text = f"Your GBU Password Reset OTP is: {otp}. This code is valid for 5 minutes."
    msg.set_content(fallback_text)
    
    # 2. Render the HTML template with your dynamic variables
    try:
        template = template_env.get_template("otp_email.html")
        html_content = template.render(
            otp=otp,
            expiry_minutes=5,
            support_email="support@gbu.ac.in"
        )
        
        # 3. Attach the HTML payload
        msg.add_alternative(html_content, subtype="html")
        
    except Exception as e:
        print(f"Template rendering failed: {e}")
        # Email will still send, but only with the plain-text fallback
    
    # 4. Dispatch the email
    try:
        with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
            server.send_message(msg)
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")