#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
数据库迁移脚本 - 添加邮箱验证字段
"""
import os
import sys

# 添加项目路径到 Python 路径
project_root = os.path.abspath(os.path.join(os.path.dirname(__file__), '../../../..'))
sys.path.insert(0, project_root)

from loguru import logger
import peewee as pw
from playhouse.migrate import migrate, SqliteMigrator

from chatpilot.apps.db import DB
from chatpilot.apps.web.models.users import User
from chatpilot.apps.web.models.auths import Auth


def check_table_exists(db, table_name):
    """检查表是否存在"""
    try:
        cursor = db.execute_sql(f"SELECT name FROM sqlite_master WHERE type='table' AND name='{table_name}';")
        return len(cursor.fetchall()) > 0
    except Exception as e:
        logger.error(f"Error checking table existence: {e}")
        return False


def check_column_exists(db, table_name, column_name):
    """检查列是否存在"""
    try:
        cursor = db.execute_sql(f"PRAGMA table_info({table_name});")
        columns = [row[1] for row in cursor.fetchall()]
        return column_name in columns
    except Exception as e:
        logger.error(f"Error checking column existence: {e}")
        return False


def migrate_user_table():
    """迁移 User 表，添加邮箱验证字段"""
    logger.info("开始迁移 User 表...")
    
    migrator = SqliteMigrator(DB)
    
    try:
        # 检查表是否存在
        if not check_table_exists(DB, 'user'):
            logger.warning("User 表不存在，创建新表...")
            DB.create_tables([User])
            logger.info("User 表创建成功")
            return True
        
        # 检查各个字段是否存在，如果不存在则添加
        migrations = []
        
        if not check_column_exists(DB, 'user', 'email_verified'):
            logger.info("添加 email_verified 字段...")
            migrations.append(
                migrator.add_column('user', 'email_verified', pw.BooleanField(default=False))
            )
        
        if not check_column_exists(DB, 'user', 'verification_token'):
            logger.info("添加 verification_token 字段...")
            migrations.append(
                migrator.add_column('user', 'verification_token', pw.CharField(null=True))
            )
        
        if not check_column_exists(DB, 'user', 'verification_expires'):
            logger.info("添加 verification_expires 字段...")
            migrations.append(
                migrator.add_column('user', 'verification_expires', pw.DateTimeField(null=True))
            )
        
        # 执行迁移
        if migrations:
            migrate(*migrations)
            logger.info(f"User 表迁移完成，添加了 {len(migrations)} 个字段")
        else:
            logger.info("User 表无需迁移，所有字段已存在")
        
        return True
        
    except Exception as e:
        logger.error(f"User 表迁移失败: {e}")
        return False


def migrate_auth_table():
    """迁移 Auth 表，添加邮箱验证字段"""
    logger.info("开始迁移 Auth 表...")
    
    migrator = SqliteMigrator(DB)
    
    try:
        # 检查表是否存在
        if not check_table_exists(DB, 'auth'):
            logger.warning("Auth 表不存在，创建新表...")
            DB.create_tables([Auth])
            logger.info("Auth 表创建成功")
            return True
        
        # 检查各个字段是否存在，如果不存在则添加
        migrations = []
        
        if not check_column_exists(DB, 'auth', 'email_verified'):
            logger.info("添加 email_verified 字段...")
            migrations.append(
                migrator.add_column('auth', 'email_verified', pw.BooleanField(default=False))
            )
        
        if not check_column_exists(DB, 'auth', 'verification_token'):
            logger.info("添加 verification_token 字段...")
            migrations.append(
                migrator.add_column('auth', 'verification_token', pw.CharField(null=True))
            )
        
        if not check_column_exists(DB, 'auth', 'verification_expires'):
            logger.info("添加 verification_expires 字段...")
            migrations.append(
                migrator.add_column('auth', 'verification_expires', pw.DateTimeField(null=True))
            )
        
        # 执行迁移
        if migrations:
            migrate(*migrations)
            logger.info(f"Auth 表迁移完成，添加了 {len(migrations)} 个字段")
        else:
            logger.info("Auth 表无需迁移，所有字段已存在")
        
        return True
        
    except Exception as e:
        logger.error(f"Auth 表迁移失败: {e}")
        return False


def migrate_existing_users():
    """迁移现有用户，设置默认邮箱验证状态"""
    logger.info("开始迁移现有用户...")
    
    try:
        # 获取所有现有用户
        users = User.select()
        updated_count = 0
        
        for user in users:
            # 如果用户的邮箱验证状态未设置，设置为已验证（向后兼容）
            if hasattr(user, 'email_verified') and user.email_verified is None:
                User.update(email_verified=True).where(User.id == user.id).execute()
                updated_count += 1
        
        if updated_count > 0:
            logger.info(f"已将 {updated_count} 个现有用户设置为邮箱已验证状态")
        else:
            logger.info("所有现有用户的邮箱验证状态已正确设置")
        
        return True
        
    except Exception as e:
        logger.error(f"迁移现有用户失败: {e}")
        return False


def run_migration():
    """运行完整的数据库迁移"""
    logger.info("🚀 开始数据库迁移...")
    
    try:
        # 连接数据库
        DB.connect()
        logger.info("数据库连接成功")
        
        # 执行迁移
        success = True
        
        # 迁移 User 表
        if not migrate_user_table():
            success = False
        
        # 迁移 Auth 表
        if not migrate_auth_table():
            success = False
        
        # 迁移现有用户
        if not migrate_existing_users():
            success = False
        
        if success:
            logger.info("✅ 数据库迁移完成！")
            logger.info("现在可以使用邮箱验证功能了")
        else:
            logger.error("❌ 数据库迁移失败")
            return False
        
        return True
        
    except Exception as e:
        logger.error(f"数据库迁移过程中发生错误: {e}")
        return False
    finally:
        try:
            DB.close()
        except:
            pass


def main():
    """主函数"""
    print("=" * 60)
    print("📧 Econometrics Agent - 邮箱验证数据库迁移")
    print("=" * 60)
    
    # 检查数据库文件是否存在
    from chatpilot.config import DB_PATH
    if os.path.exists(DB_PATH):
        logger.info(f"数据库文件位置: {DB_PATH}")
    else:
        logger.warning(f"数据库文件不存在: {DB_PATH}")
        logger.info("迁移脚本将创建必要的表结构")
    
    # 运行迁移
    success = run_migration()
    
    if success:
        print("\n✅ 迁移成功完成！")
        print("\n📋 后续步骤:")
        print("1. 配置 SMTP 邮件服务 (.env 文件)")
        print("2. 设置 EMAIL_VERIFICATION_ENABLED=true")
        print("3. 重启应用服务")
        print("4. 测试用户注册和邮箱验证功能")
    else:
        print("\n❌ 迁移失败！")
        print("请检查错误日志并重试")
        sys.exit(1)


if __name__ == "__main__":
    main()