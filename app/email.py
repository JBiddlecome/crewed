import smtplib
import ssl
from email.message import EmailMessage
from typing import Optional

from fastapi import BackgroundTasks
from fastapi.templating import Jinja2Templates

from app.config import SMTP_HOST, SMTP_PORT, SMTP_USER, SMTP_PASSWORD, FROM_EMAIL

templates = Jinja2Templates(directory="templates")

def _send_email_sync(to_email: str, subject: str, html_body: str, text_body: Optional[str] = None):
    if not SMTP_HOST:
        print(f"Warning: SMTP_HOST not configured. Would have sent email to {to_email} with subject '{subject}'")
        return

    msg = EmailMessage()
    msg['Subject'] = subject
    msg['From'] = FROM_EMAIL
    msg['To'] = to_email
    
    if text_body:
        msg.set_content(text_body)
        msg.add_alternative(html_body, subtype='html')
    else:
        msg.set_content(html_body, subtype='html')

    try:
        if SMTP_PORT == 465:
            context = ssl.create_default_context()
            with smtplib.SMTP_SSL(SMTP_HOST, SMTP_PORT, context=context) as server:
                if SMTP_USER and SMTP_PASSWORD:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        else:
            with smtplib.SMTP(SMTP_HOST, SMTP_PORT) as server:
                server.starttls()
                if SMTP_USER and SMTP_PASSWORD:
                    server.login(SMTP_USER, SMTP_PASSWORD)
                server.send_message(msg)
        print(f"Email successfully sent to {to_email}")
    except Exception as e:
        print(f"Failed to send email to {to_email}: {e}")

def send_email(background_tasks: BackgroundTasks, to_email: str, subject: str, html_body: str, text_body: Optional[str] = None):
    """Schedules an email to be sent in the background."""
    background_tasks.add_task(_send_email_sync, to_email, subject, html_body, text_body)

def send_confirmation_email(background_tasks: BackgroundTasks, request, user_email: str, token: str):
    subject = "Confirm Your Email - Crewed"
    confirm_url = str(request.url_for("confirm_email")) + f"?token={token}"
    
    # We will create an email template for this
    # For now we can use a basic template or render one
    template = templates.get_template("emails/confirm_email.html")
    html_body = template.render({"request": request, "confirm_url": confirm_url, "user_email": user_email})
    
    send_email(background_tasks, user_email, subject, html_body)

def send_password_reset_email(background_tasks: BackgroundTasks, request, user_email: str, token: str):
    subject = "Reset Your Password - Crewed"
    reset_url = str(request.url_for("reset_password")) + f"?token={token}"
    
    template = templates.get_template("emails/reset_password.html")
    html_body = template.render({"request": request, "reset_url": reset_url, "user_email": user_email})
    
    send_email(background_tasks, user_email, subject, html_body)
