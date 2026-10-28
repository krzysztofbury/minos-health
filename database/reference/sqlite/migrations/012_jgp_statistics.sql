ALTER TABLE schema_migration RENAME TO previous_schema_migration;
CREATE TABLE schema_migration (
    version INTEGER PRIMARY KEY CHECK (version BETWEEN 1 AND 12),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64)
) STRICT;

CREATE TABLE jgp_statistics_release (
    id TEXT PRIMARY KEY CHECK (length(id) BETWEEN 1 AND 120),
    manifest_json TEXT NOT NULL CHECK (json_valid(manifest_json) AND length(manifest_json) <= 65536)
) STRICT;

-- One pinned NFZ monthly report. Each reports three months (#283).
CREATE TABLE jgp_statistics_report (
    release_id TEXT NOT NULL REFERENCES jgp_statistics_release(id),
    report_month TEXT NOT NULL CHECK (report_month GLOB '20[0-9][0-9]-[01][0-9]'),
    published_on TEXT NOT NULL CHECK (published_on = date(published_on)),
    sha256 TEXT NOT NULL CHECK (length(sha256) = 64),
    PRIMARY KEY (release_id, report_month)
) STRICT;

-- Every figure of every report: revisions stay visible, and a reader picks
-- the newest report for a month. hospitalizations is NULL when the report has
-- no count. suppressed marks the NFZ marker for one to four (written as <5).
CREATE TABLE jgp_group_statistic (
    release_id TEXT NOT NULL,
    code TEXT NOT NULL CHECK (length(code) BETWEEN 2 AND 8),
    hospital_level TEXT NOT NULL CHECK (length(hospital_level) BETWEEN 1 AND 64),
    month TEXT NOT NULL CHECK (month GLOB '20[0-9][0-9]-[01][0-9]'),
    report_month TEXT NOT NULL,
    age_months INTEGER NOT NULL CHECK (age_months IN (0, 1, 12)),
    section TEXT NOT NULL CHECK (length(section) BETWEEN 1 AND 160),
    hospitalizations INTEGER CHECK (hospitalizations IS NULL OR hospitalizations >= 0),
    suppressed INTEGER NOT NULL CHECK (suppressed IN (0, 1)),
    points_ten_thousandths INTEGER CHECK (points_ten_thousandths IS NULL OR points_ten_thousandths >= 0),
    CHECK (NOT (suppressed = 1 AND hospitalizations IS NOT NULL)),
    PRIMARY KEY (release_id, code, month, hospital_level, report_month),
    FOREIGN KEY (release_id, report_month) REFERENCES jgp_statistics_report(release_id, report_month)
) STRICT, WITHOUT ROWID;

CREATE TABLE jgp_section_statistic (
    release_id TEXT NOT NULL,
    section TEXT NOT NULL CHECK (length(section) BETWEEN 1 AND 160),
    admission TEXT NOT NULL CHECK (admission IN ('planned', 'other')),
    character TEXT NOT NULL CHECK (character IN ('procedural', 'conservative')),
    month TEXT NOT NULL CHECK (month GLOB '20[0-9][0-9]-[01][0-9]'),
    report_month TEXT NOT NULL,
    age_months INTEGER NOT NULL CHECK (age_months IN (0, 1, 12)),
    hospitalizations INTEGER CHECK (hospitalizations IS NULL OR hospitalizations >= 0),
    suppressed INTEGER NOT NULL CHECK (suppressed IN (0, 1)),
    mean_stay_hundredths INTEGER CHECK (mean_stay_hundredths IS NULL OR mean_stay_hundredths >= 0),
    mean_age_tenths INTEGER CHECK (mean_age_tenths IS NULL OR mean_age_tenths >= 0),
    CHECK (NOT (suppressed = 1 AND hospitalizations IS NOT NULL)),
    PRIMARY KEY (release_id, section, month, admission, character, report_month),
    FOREIGN KEY (release_id, report_month) REFERENCES jgp_statistics_report(release_id, report_month)
) STRICT, WITHOUT ROWID;

INSERT INTO schema_migration SELECT version, sha256 FROM previous_schema_migration;
DROP TABLE previous_schema_migration;
