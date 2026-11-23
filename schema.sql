CREATE DATABASE IF NOT EXISTS news_summary
  DEFAULT CHARACTER SET utf8mb4
  DEFAULT COLLATE utf8mb4_unicode_ci;
USE news_summary;

CREATE TABLE IF NOT EXISTS users (
  user_id        INT AUTO_INCREMENT PRIMARY KEY,
  email          VARCHAR(255) NOT NULL UNIQUE,
  password_hash  VARCHAR(255) NOT NULL,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS articles (
  article_id    INT AUTO_INCREMENT PRIMARY KEY,
  user_id       INT NOT NULL,
  title         VARCHAR(300) NOT NULL,
  source_press  VARCHAR(100),
  source        ENUM('MANUAL','RSS') NOT NULL DEFAULT 'MANUAL',
  url           VARCHAR(500),
  raw_text      TEXT NOT NULL,
  created_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at    DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_articles_user FOREIGN KEY (user_id)
    REFERENCES users(user_id) ON DELETE CASCADE,
  INDEX idx_articles_user_created (user_id, created_at)
);

CREATE TABLE IF NOT EXISTS summaries (
  result_id      INT AUTO_INCREMENT PRIMARY KEY,
  article_id     INT NOT NULL,
  summary_level  ENUM('ONE_LINE','SHORT','DETAIL','KEY_EVENTS') NOT NULL,
  summary_text   TEXT NOT NULL,
  people_orgs    JSON,
  keywords       JSON,
  theme          TEXT,
  created_at     DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_summaries_article FOREIGN KEY (article_id)
    REFERENCES articles(article_id) ON DELETE CASCADE,
  CONSTRAINT uq_summaries_article_level UNIQUE (article_id, summary_level)
);

CREATE TABLE IF NOT EXISTS memos (
  memo_id     INT AUTO_INCREMENT PRIMARY KEY,
  article_id  INT NOT NULL,
  memo_text   TEXT NOT NULL,
  created_at  DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  CONSTRAINT fk_memos_article FOREIGN KEY (article_id)
    REFERENCES articles(article_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS rss_sources (
  rss_id          INT AUTO_INCREMENT PRIMARY KEY,
  user_id         INT NOT NULL,
  feed_url        VARCHAR(500) NOT NULL,
  poll_interval   INT NOT NULL DEFAULT 60,
  notify_channel  ENUM('EMAIL','DISCORD') NOT NULL DEFAULT 'EMAIL',
  notify_target   VARCHAR(500),
  CONSTRAINT fk_rss_user FOREIGN KEY (user_id)
    REFERENCES users(user_id) ON DELETE CASCADE,
  UNIQUE KEY uq_rss_user_feed (user_id, feed_url(255))
);

CREATE TABLE IF NOT EXISTS user_preferences (
  user_id          INT PRIMARY KEY,
  categories       JSON NOT NULL,
  sort_mode        ENUM('LATEST','POPULAR') NOT NULL DEFAULT 'LATEST',
  item_count       INT NOT NULL DEFAULT 5,
  summary_level    ENUM('ONE_LINE','SHORT','DETAIL','KEY_EVENTS') NOT NULL DEFAULT 'SHORT',
  send_time        TIME NOT NULL DEFAULT '08:00:00',
  discord_webhook  VARCHAR(500) NULL,
  last_notified_on DATE NULL,
  updated_at       DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
  CONSTRAINT fk_pref_user FOREIGN KEY (user_id)
    REFERENCES users(user_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS collected_news (
  news_id        INT AUTO_INCREMENT PRIMARY KEY,
  category       VARCHAR(20) NOT NULL,
  title          VARCHAR(300) NOT NULL,
  excerpt        TEXT NULL,
  source_press   VARCHAR(100),
  url            TEXT NOT NULL,
  url_hash       CHAR(64) NOT NULL,
  published_at   DATETIME NULL,
  rank_no        INT NOT NULL DEFAULT 0,
  collected_at   DATETIME NOT NULL DEFAULT CURRENT_TIMESTAMP,
  UNIQUE KEY uq_collected_category_url (category, url_hash),
  INDEX idx_collected_category_time (category, collected_at)
);

INSERT IGNORE INTO users (user_id, email, password_hash)
VALUES (1, 'test@example.com', 'dummy_hash');
