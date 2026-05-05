[
  {
    "table_name": "candidate_pool",
    "column_name": "id",
    "data_type": "integer",
    "is_nullable": "NO",
    "column_default": "nextval('candidate_pool_id_seq'::regclass)"
  },
  {
    "table_name": "candidate_pool",
    "column_name": "name_cn",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "candidate_pool",
    "column_name": "name_en",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "candidate_pool",
    "column_name": "email",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "candidate_pool",
    "column_name": "phone",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "candidate_pool",
    "column_name": "company",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "candidate_pool",
    "column_name": "department",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "candidate_pool",
    "column_name": "employee_id",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "candidate_pool",
    "column_name": "country",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "candidate_pool",
    "column_name": "position",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "candidate_pool",
    "column_name": "created_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": "now()"
  },
  {
    "table_name": "candidate_pool",
    "column_name": "updated_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": "now()"
  },
  {
    "table_name": "countries",
    "column_name": "code",
    "data_type": "character varying",
    "is_nullable": "NO",
    "column_default": null
  },
  {
    "table_name": "countries",
    "column_name": "name_zh",
    "data_type": "character varying",
    "is_nullable": "NO",
    "column_default": null
  },
  {
    "table_name": "countries",
    "column_name": "name_en",
    "data_type": "character varying",
    "is_nullable": "NO",
    "column_default": null
  },
  {
    "table_name": "email_otps",
    "column_name": "email",
    "data_type": "text",
    "is_nullable": "NO",
    "column_default": null
  },
  {
    "table_name": "email_otps",
    "column_name": "code",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "email_otps",
    "column_name": "expires_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exam_assignments",
    "column_name": "id",
    "data_type": "integer",
    "is_nullable": "NO",
    "column_default": "nextval('exam_assignments_id_seq'::regclass)"
  },
  {
    "table_name": "exam_assignments",
    "column_name": "exam_id",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exam_assignments",
    "column_name": "user_id",
    "data_type": "uuid",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exam_assignments",
    "column_name": "assigned_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": "now()"
  },
  {
    "table_name": "exam_results",
    "column_name": "id",
    "data_type": "integer",
    "is_nullable": "NO",
    "column_default": "nextval('exam_results_id_seq'::regclass)"
  },
  {
    "table_name": "exam_results",
    "column_name": "user_id",
    "data_type": "uuid",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exam_results",
    "column_name": "exam_id",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exam_results",
    "column_name": "answers",
    "data_type": "jsonb",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exam_results",
    "column_name": "details",
    "data_type": "jsonb",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exam_results",
    "column_name": "total_score",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exam_results",
    "column_name": "custom1",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exam_results",
    "column_name": "custom2",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exam_results",
    "column_name": "custom3",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exam_results",
    "column_name": "custom4",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exam_results",
    "column_name": "custom5",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exam_results",
    "column_name": "created_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": "now()"
  },
  {
    "table_name": "exam_results",
    "column_name": "submit_method",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": "'manual'::character varying"
  },
  {
    "table_name": "exam_results",
    "column_name": "time_used",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exams",
    "column_name": "id",
    "data_type": "integer",
    "is_nullable": "NO",
    "column_default": "nextval('exams_id_seq'::regclass)"
  },
  {
    "table_name": "exams",
    "column_name": "title",
    "data_type": "text",
    "is_nullable": "NO",
    "column_default": null
  },
  {
    "table_name": "exams",
    "column_name": "is_active",
    "data_type": "boolean",
    "is_nullable": "YES",
    "column_default": "false"
  },
  {
    "table_name": "exams",
    "column_name": "duration",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": "60"
  },
  {
    "table_name": "exams",
    "column_name": "total_score",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": "100"
  },
  {
    "table_name": "exams",
    "column_name": "questions_count",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": "0"
  },
  {
    "table_name": "exams",
    "column_name": "created_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": "now()"
  },
  {
    "table_name": "exams",
    "column_name": "start_time",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exams",
    "column_name": "end_time",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exams",
    "column_name": "status",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": "'draft'::character varying"
  },
  {
    "table_name": "exams",
    "column_name": "reviewer",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exams",
    "column_name": "created_by",
    "data_type": "uuid",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exams",
    "column_name": "quarter",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exams",
    "column_name": "deleted_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "exams",
    "column_name": "country",
    "data_type": "character varying",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "interview_results",
    "column_name": "id",
    "data_type": "integer",
    "is_nullable": "NO",
    "column_default": "nextval('interview_results_id_seq'::regclass)"
  },
  {
    "table_name": "interview_results",
    "column_name": "interview_id",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "interview_results",
    "column_name": "user_id",
    "data_type": "uuid",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "interview_results",
    "column_name": "question_id",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "interview_results",
    "column_name": "answer",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "interview_results",
    "column_name": "is_correct",
    "data_type": "boolean",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "interview_results",
    "column_name": "feedback",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "interview_results",
    "column_name": "submitted_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "interview_results",
    "column_name": "created_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": "now()"
  },
  {
    "table_name": "interviews",
    "column_name": "id",
    "data_type": "integer",
    "is_nullable": "NO",
    "column_default": "nextval('interviews_id_seq'::regclass)"
  },
  {
    "table_name": "interviews",
    "column_name": "title",
    "data_type": "text",
    "is_nullable": "NO",
    "column_default": null
  },
  {
    "table_name": "interviews",
    "column_name": "exam_id",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "interviews",
    "column_name": "created_by",
    "data_type": "uuid",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "interviews",
    "column_name": "reviewer",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": "''::text"
  },
  {
    "table_name": "interviews",
    "column_name": "status",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": "'draft'::text"
  },
  {
    "table_name": "interviews",
    "column_name": "start_time",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "interviews",
    "column_name": "end_time",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "interviews",
    "column_name": "question_count",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": "5"
  },
  {
    "table_name": "interviews",
    "column_name": "created_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": "now()"
  },
  {
    "table_name": "interviews",
    "column_name": "updated_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": "now()"
  },
  {
    "table_name": "interviews",
    "column_name": "deleted_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "interviews",
    "column_name": "interviewee_count",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": "0"
  },
  {
    "table_name": "questions",
    "column_name": "id",
    "data_type": "integer",
    "is_nullable": "NO",
    "column_default": "nextval('questions_id_seq'::regclass)"
  },
  {
    "table_name": "questions",
    "column_name": "exam_id",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "questions",
    "column_name": "num",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "questions",
    "column_name": "type",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "questions",
    "column_name": "content",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "questions",
    "column_name": "options",
    "data_type": "jsonb",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "questions",
    "column_name": "answer",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "questions",
    "column_name": "score",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "questions",
    "column_name": "content_raw",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "questions",
    "column_name": "content_cn",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "questions",
    "column_name": "content_en",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "questions",
    "column_name": "options_raw",
    "data_type": "jsonb",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "questions",
    "column_name": "options_struct",
    "data_type": "jsonb",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "system_config",
    "column_name": "key",
    "data_type": "text",
    "is_nullable": "NO",
    "column_default": null
  },
  {
    "table_name": "system_config",
    "column_name": "value",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "system_config",
    "column_name": "updated_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": "now()"
  },
  {
    "table_name": "system_configs",
    "column_name": "key",
    "data_type": "character varying",
    "is_nullable": "NO",
    "column_default": null
  },
  {
    "table_name": "system_configs",
    "column_name": "value",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "system_configs",
    "column_name": "updated_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "YES",
    "column_default": "now()"
  },
  {
    "table_name": "table_name",
    "column_name": "id",
    "data_type": "bigint",
    "is_nullable": "NO",
    "column_default": null
  },
  {
    "table_name": "table_name",
    "column_name": "inserted_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "NO",
    "column_default": "timezone('utc'::text, now())"
  },
  {
    "table_name": "table_name",
    "column_name": "updated_at",
    "data_type": "timestamp with time zone",
    "is_nullable": "NO",
    "column_default": "timezone('utc'::text, now())"
  },
  {
    "table_name": "table_name",
    "column_name": "data",
    "data_type": "jsonb",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "table_name",
    "column_name": "name",
    "data_type": "text",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "training_attendances",
    "column_name": "id",
    "data_type": "integer",
    "is_nullable": "NO",
    "column_default": "nextval('training_attendances_id_seq'::regclass)"
  },
  {
    "table_name": "training_attendances",
    "column_name": "training_id",
    "data_type": "integer",
    "is_nullable": "YES",
    "column_default": null
  },
  {
    "table_name": "training_attendances",
    "column_name": "user_id",
    "data_type": "uuid",
    "is_nullable": "YES",
    "column_default": null
  }
]