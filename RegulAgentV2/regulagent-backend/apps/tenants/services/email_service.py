import django.core.mail
from django.conf import settings


def send_welcome_email(user, temp_password: str) -> None:
    login_url = f"{settings.FRONTEND_URL}/signin"
    subject = "Welcome to RegulAgent — Your account is ready"
    message = (
        f"Hello {user.first_name or user.email},\n\n"
        f"Your RegulAgent account has been created.\n\n"
        f"Email: {user.email}\n"
        f"Temporary password: {temp_password}\n\n"
        f"Login at: {login_url}\n\n"
        f"Please change your password after your first login.\n\n"
        f"— The RegulAgent Team"
    )
    django.core.mail.send_mail(
        subject=subject,
        message=message,
        from_email=settings.DEFAULT_FROM_EMAIL,
        recipient_list=[user.email],
        fail_silently=True,
    )
