CREATE TABLE tasks (
  id         CHAR(32)         PRIMARY KEY,
  total      INT              NOT NULL,
  sent       INT              NOT NULL DEFAULT 0,
  failed     INT              NOT NULL DEFAULT 0,
  skipped    INT              NOT NULL DEFAULT 0,
  pending    INT              NOT NULL DEFAULT 0,
  finished   TINYINT(1)       NOT NULL DEFAULT 0,
  created_at DATETIME         NOT NULL DEFAULT CURRENT_TIMESTAMP,
  updated_at DATETIME         NOT NULL 
    ON UPDATE CURRENT_TIMESTAMP 
    NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE log_entries (
  id         BIGINT AUTO_INCREMENT PRIMARY KEY,
  task_id    CHAR(32)              NOT NULL,
  message    TEXT                   NOT NULL,
  timestamp  DATETIME               NOT NULL DEFAULT CURRENT_TIMESTAMP,
  INDEX(task_id),
  FOREIGN KEY (task_id) REFERENCES tasks(id) ON DELETE CASCADE
);

ALTER TABLE tasks
  ADD COLUMN closed TINYINT(1) NOT NULL DEFAULT 0;
