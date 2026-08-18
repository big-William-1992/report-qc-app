"""
server/models.py — SQLAlchemy ORM 模型（多用户 / 科室自托管）
===========================================================
实体关系：
- Department（科室）1──* User（用户/账号）
- Department 1──* Sample（样本归属科室）
- User 1──* Sample（样本归属质控责任人）
- User 1──* QueueItem（待质控队列项归属提交人）
- Setting 既可是全局（user_id=NULL）也可是用户级（user_id 指向用户）

说明：samples 表字段刻意对齐原 src/samplelib.py 的 SQLite 结构，
迁移时旧逻辑可平滑接入；新增 dept_id 实现科室级数据归属。
"""
import datetime

from sqlalchemy import (
    Column, Integer, String, Text, DateTime, ForeignKey, Index,
)
from sqlalchemy.orm import relationship
from server.db import Base


class Department(Base):
    __tablename__ = "departments"
    id = Column(Integer, primary_key=True)
    name = Column(String(120), unique=True, nullable=False, comment="科室名，唯一")
    created_at = Column(DateTime, default=datetime.datetime.now)


class User(Base):
    __tablename__ = "users"
    id = Column(Integer, primary_key=True)
    emp_id = Column(String(64), unique=True, nullable=False, comment="工号，登录名")
    name = Column(String(120), default="")
    pwd_hash = Column(String(256), nullable=False)
    salt = Column(String(64), nullable=False)
    role = Column(String(20), default="doctor", comment="admin | doctor")
    dept_id = Column(Integer, ForeignKey("departments.id"), nullable=True, comment="所属科室")
    created_at = Column(DateTime, default=datetime.datetime.now)
    department = relationship("Department")

    __table_args__ = (Index("ix_users_emp_id", "emp_id"),)


class Sample(Base):
    __tablename__ = "samples"
    id = Column(Integer, primary_key=True)
    ts = Column(String(32), nullable=False, comment="采集时间 ISO")
    patient = Column(String(120))
    gender = Column(String(16))
    age = Column(String(16))
    modality = Column(String(64))
    applied_site = Column(String(120), comment="检查部位")
    laterality = Column(String(32), comment="侧别")
    user_id = Column(String(64), nullable=True, comment="质控责任人（工号，与 samplelib 一致；2026-08-18 由 Integer FK 修正）")
    dept_id = Column(String(64), nullable=True, comment="归属科室（samplelib 原生层为 TEXT，2026-08-18 统一声明）")
    report_text = Column(Text)
    findings_json = Column(Text, comment="质控发现（JSON 数组）")
    scores_json = Column(Text, comment="四维评分（JSON）")
    created_at = Column(DateTime, default=datetime.datetime.now)


class QueueItem(Base):
    __tablename__ = "queue"
    id = Column(Integer, primary_key=True)
    report_text = Column(Text)
    meta_json = Column(Text, comment="病人信息/检查类型等（JSON）")
    status = Column(String(20), default="pending", comment="pending | done")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True, comment="提交人")
    report_hash = Column(String(32), unique=True, nullable=True,
                         comment="正文去重指纹（MD5，数据库层防并发重复入队，2026-08-18）")
    created_at = Column(DateTime, default=datetime.datetime.now)


class Setting(Base):
    __tablename__ = "settings"
    id = Column(Integer, primary_key=True)
    key = Column(String(120), unique=True, nullable=False, comment="设置键")
    value_json = Column(Text, comment="设置值（JSON）")
    user_id = Column(Integer, ForeignKey("users.id"), nullable=True,
                    comment="NULL=全局设置；非 NULL=用户级覆盖")
