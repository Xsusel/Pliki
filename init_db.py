import os
from app import app, db, User
from werkzeug.security import generate_password_hash
import getpass

def init_db():
    with app.app_context():
        # Create database file
        if not os.path.exists('instance'):
            os.makedirs('instance')

        db.create_all()
        print("Baza danych została zainicjalizowana.")

        # Check if user exists
        if User.query.first():
            print("Użytkownik administratora już istnieje.")
            return

        print("Tworzenie konta administratora...")
        username = input("Podaj nazwę użytkownika (domyślnie 'admin'): ").strip()
        if not username:
            username = "admin"

        while True:
            password = getpass.getpass("Podaj hasło: ")
            confirm_password = getpass.getpass("Potwierdź hasło: ")

            if password == confirm_password and password:
                break
            print("Hasła nie są identyczne lub są puste. Spróbuj ponownie.")

        hashed_password = generate_password_hash(password)
        new_user = User(username=username, password=hashed_password)
        db.session.add(new_user)
        db.session.commit()
        print(f"Użytkownik '{username}' został utworzony pomyślnie.")

if __name__ == "__main__":
    init_db()
