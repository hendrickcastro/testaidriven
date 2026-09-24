WITH ok AS (
    SELECT user_id, date(ts) AS d
    FROM logins
    WHERE success = 1
),
days AS (
    SELECT user_id, d, COUNT(*) AS n
    FROM ok
    GROUP BY user_id, d
),
grouped AS (
    SELECT user_id, d, n,
           julianday(d) - ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY d) AS grp
    FROM days
),
streaks AS (
    SELECT user_id, MIN(d) AS s, MAX(d) AS e, COUNT(*) AS len, SUM(n) AS logins
    FROM grouped
    GROUP BY user_id, grp
),
best AS (
    SELECT user_id, s, e, len, logins,
           ROW_NUMBER() OVER (PARTITION BY user_id ORDER BY len DESC, s ASC) AS rn
    FROM streaks
),
per_user AS (
    SELECT u.username,
           COALESCE(b.len, 0) AS streak_days,
           b.s AS streak_start,
           b.e AS streak_end,
           COALESCE(b.logins, 0) AS logins_in_streak
    FROM users u
    LEFT JOIN best b ON b.user_id = u.id AND b.rn = 1
)
SELECT username, streak_days, streak_start, streak_end, logins_in_streak,
       DENSE_RANK() OVER (ORDER BY streak_days DESC) AS rnk
FROM per_user
ORDER BY streak_days DESC, username ASC;
