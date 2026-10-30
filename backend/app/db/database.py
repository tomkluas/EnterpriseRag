from sqlalchemy import create_engine
from sqlalchemy.orm import sessionmaker
from app.core.paths import get_data_dir

_db_path = get_data_dir() / "rag_system.db"

engine = create_engine(
    f"sqlite:///{_db_path}",
    connect_args={"check_same_thread": False},
)

SessionLocal = sessionmaker(autocommit=False, autoflush=False, bind=engine)


def create_tables():
    from app.db.models import Base
    Base.metadata.create_all(bind=engine)
