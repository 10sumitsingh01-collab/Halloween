from sqlalchemy import Column, String
from app.db import Base


class BotState(Base):
    __tablename__ = "bot_state"

    key = Column(String(64), primary_key=True)
    value = Column(String(255), nullable=False)
