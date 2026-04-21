# seed.py
import json
import sys
from pathlib import Path
from app.database import SessionLocal, engine
from app.models import Base, Profile
from uuid6 import uuid7



sys.path.append(str(Path(__file__).resolve().parent))



def seed():
    Base.metadata.create_all(bind=engine)

    seed_path = Path(__file__).resolve().parent / "seed_profiles.json"

    if not seed_path.exists():
        print(f"[seed] seed_profiles.json not found at {seed_path}")
        return

    with open(seed_path, "r", encoding="utf-8") as f:
        data = json.load(f)

    profiles = data.get("profiles", [])

    if not profiles:
        print("[seed] No profiles found in seed file.")
        return

    db = SessionLocal()

    try:
        # Fetch ALL existing names in ONE query instead of 2026 queries
        existing_names = set(
            row[0] for row in db.query(Profile.name).all()
        )
        print(f"[seed] Found {len(existing_names)} existing records.")

        to_insert = []

        for record in profiles:
            if record["name"] in existing_names:
                continue

            to_insert.append(Profile(
                id=str(uuid7()),
                name=record["name"],
                gender=record["gender"],
                gender_probability=record["gender_probability"],
                age=record["age"],
                age_group=record["age_group"],
                country_id=record["country_id"],
                country_name=record["country_name"],
                country_probability=record["country_probability"],
            ))

        if not to_insert:
            print("[seed] Nothing to insert — all records already exist.")
            return

        # Insert all at once in a single transaction
        db.bulk_save_objects(to_insert)
        db.commit()
        print(f"[seed] Done — {len(to_insert)} inserted, {len(existing_names)} skipped.")

    except Exception as e:
        db.rollback()
        print(f"[seed] Error: {e}")
        raise

    finally:
        db.close()


if __name__ == "__main__":
    seed()