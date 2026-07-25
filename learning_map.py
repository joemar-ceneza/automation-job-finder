"""
learning_map.py
A curated map of what it takes to learn each skill: rough difficulty, an hours
estimate, what you should know first, and where to start reading.

Hand-maintained on purpose. A model asked for course URLs produces plausible
ones that 404, so the links here are official documentation a human checked, and
AI mode is only ever allowed to write prose *around* them. The hours are
deliberately rough — they exist to make a plan addable ("about 60 hours to close
your top three gaps"), not to promise a schedule. Tune them to your own pace;
they are yours, not a model's invention.

Each entry is (difficulty, hours, prerequisites, resources). Prerequisites name
other skills — the planner uses them to put foundations before the thing that
needs them, and to pull in a missing foundation you don't have yet. Names match
config.MASTER_SKILLS / skill_lexicon so a "missing skill" can be looked up
directly. A skill with no entry still appears in a plan, just without an
estimate — the map never has to be complete to be useful.
"""
from dataclasses import dataclass, field


@dataclass(frozen=True)
class LearningEntry:
    """What it takes to pick up one skill."""
    difficulty: str = "medium"                  # easy | medium | hard
    hours: int = 20
    prerequisites: tuple[str, ...] = ()
    resources: tuple[tuple[str, str], ...] = ()  # (label, url)


def _entry(difficulty: str, hours: int, prerequisites=(), resources=()):
    return LearningEntry(difficulty, hours, tuple(prerequisites),
                         tuple(resources))


# Skill -> what it takes. Extend freely; an unknown skill degrades gracefully.
LEARNING_MAP: dict[str, LearningEntry] = {
    # --- foundations ----------------------------------------------------
    "Git": _entry("easy", 10, (), (
        ("Official Git docs", "https://git-scm.com/doc"),)),
    "Linux": _entry("medium", 25, (), (
        ("Linux Journey (free)", "https://linuxjourney.com/"),)),
    "Bash Command Line": _entry("easy", 12, ("Linux",), (
        ("GNU Bash manual", "https://www.gnu.org/software/bash/manual/"),)),
    "SQL": _entry("easy", 20, (), (
        ("SQLBolt (free, interactive)", "https://sqlbolt.com/"),
        ("PostgreSQL SQL tutorial",
         "https://www.postgresql.org/docs/current/tutorial.html"))),
    "Agile": _entry("easy", 6, (), (
        ("Agile Manifesto", "https://agilemanifesto.org/"),)),
    "Scrum": _entry("easy", 8, ("Agile",), (
        ("The Scrum Guide", "https://scrumguides.org/"),)),

    # --- languages ------------------------------------------------------
    "Python": _entry("easy", 40, (), (
        ("Official Python tutorial", "https://docs.python.org/3/tutorial/"),)),
    "JavaScript ES6": _entry("medium", 40, (), (
        ("MDN JavaScript guide",
         "https://developer.mozilla.org/en-US/docs/Web/JavaScript/Guide"),)),
    "TypeScript": _entry("medium", 25, ("JavaScript ES6",), (
        ("TypeScript handbook",
         "https://www.typescriptlang.org/docs/handbook/intro.html"),)),
    "PHP": _entry("medium", 35, (), (
        ("PHP manual", "https://www.php.net/manual/en/"),)),
    "Java": _entry("hard", 60, (), (
        ("Oracle Java tutorials",
         "https://docs.oracle.com/javase/tutorial/"),)),
    "C#": _entry("medium", 45, (), (
        ("Microsoft C# docs",
         "https://learn.microsoft.com/en-us/dotnet/csharp/"),)),
    "Go": _entry("medium", 35, (), (
        ("A Tour of Go", "https://go.dev/tour/"),)),
    "Rust": _entry("hard", 70, (), (
        ("The Rust Book", "https://doc.rust-lang.org/book/"),)),

    # --- frontend -------------------------------------------------------
    "HTML 5": _entry("easy", 15, (), (
        ("MDN HTML guide",
         "https://developer.mozilla.org/en-US/docs/Learn/HTML"),)),
    "CSS 3": _entry("medium", 25, ("HTML 5",), (
        ("MDN CSS guide",
         "https://developer.mozilla.org/en-US/docs/Learn/CSS"),)),
    "Tailwind CSS": _entry("easy", 10, ("CSS 3",), (
        ("Tailwind docs", "https://tailwindcss.com/docs/installation"),)),
    "React JS": _entry("medium", 45, ("JavaScript ES6",), (
        ("React official tutorial", "https://react.dev/learn"),)),
    "Redux": _entry("medium", 15, ("React JS",), (
        ("Redux essentials",
         "https://redux.js.org/tutorials/essentials/part-1-overview-concepts"),)),
    "Next JS": _entry("medium", 25, ("React JS",), (
        ("Next.js Learn course", "https://nextjs.org/learn"),)),
    "Vue.js": _entry("medium", 35, ("JavaScript ES6",), (
        ("Vue guide", "https://vuejs.org/guide/introduction.html"),)),
    "Angular": _entry("hard", 55, ("TypeScript",), (
        ("Angular docs", "https://angular.dev/"),)),
    "Svelte": _entry("medium", 25, ("JavaScript ES6",), (
        ("Svelte tutorial", "https://svelte.dev/tutorial"),)),
    "React Native": _entry("medium", 35, ("React JS",), (
        ("React Native docs",
         "https://reactnative.dev/docs/getting-started"),)),
    "Flutter": _entry("medium", 45, (), (
        ("Flutter docs", "https://docs.flutter.dev/get-started"),)),

    # --- backend --------------------------------------------------------
    "Node JS": _entry("medium", 30, ("JavaScript ES6",), (
        ("Node.js learn", "https://nodejs.org/en/learn"),)),
    "Express JS": _entry("easy", 15, ("Node JS",), (
        ("Express guide", "https://expressjs.com/en/starter/installing.html"),)),
    "NestJS": _entry("medium", 30, ("TypeScript", "Node JS"), (
        ("NestJS docs", "https://docs.nestjs.com/"),)),
    "Django": _entry("medium", 40, ("Python",), (
        ("Django tutorial",
         "https://docs.djangoproject.com/en/stable/intro/tutorial01/"),)),
    "Flask": _entry("easy", 20, ("Python",), (
        ("Flask tutorial",
         "https://flask.palletsprojects.com/en/stable/tutorial/"),)),
    "FastAPI": _entry("easy", 20, ("Python",), (
        ("FastAPI tutorial", "https://fastapi.tiangolo.com/tutorial/"),)),
    "Laravel": _entry("medium", 40, ("PHP",), (
        ("Laravel docs", "https://laravel.com/docs"),)),
    "Symfony": _entry("hard", 50, ("PHP",), (
        ("Symfony docs", "https://symfony.com/doc/current/index.html"),)),
    "Spring Boot": _entry("hard", 55, ("Java",), (
        ("Spring guides", "https://spring.io/guides"),)),
    "ASP.NET": _entry("hard", 50, ("C#",), (
        ("ASP.NET Core docs",
         "https://learn.microsoft.com/en-us/aspnet/core/"),)),
    ".NET Core": _entry("medium", 40, ("C#",), (
        (".NET docs", "https://learn.microsoft.com/en-us/dotnet/"),)),
    "REST API": _entry("easy", 15, (), (
        ("MDN HTTP overview",
         "https://developer.mozilla.org/en-US/docs/Web/HTTP/Overview"),)),
    "GraphQL": _entry("medium", 20, ("REST API",), (
        ("GraphQL learn", "https://graphql.org/learn/"),)),
    "Microservices": _entry("hard", 40, ("Docker", "REST API"), (
        ("microservices.io patterns", "https://microservices.io/patterns/"),)),
    "WordPress": _entry("easy", 20, ("PHP",), (
        ("Learn WordPress", "https://learn.wordpress.org/"),)),

    # --- databases ------------------------------------------------------
    "PostgreSQL": _entry("medium", 25, ("SQL",), (
        ("PostgreSQL docs", "https://www.postgresql.org/docs/current/"),)),
    "MySQL": _entry("easy", 20, ("SQL",), (
        ("MySQL docs", "https://dev.mysql.com/doc/"),)),
    "MongoDB": _entry("easy", 20, (), (
        ("MongoDB manual", "https://www.mongodb.com/docs/manual/"),)),
    "Redis": _entry("easy", 12, (), (
        ("Redis docs", "https://redis.io/docs/latest/"),)),
    "Elasticsearch": _entry("hard", 35, (), (
        ("Elasticsearch guide",
         "https://www.elastic.co/guide/en/elasticsearch/reference/current/index.html"),)),
    "Snowflake": _entry("medium", 30, ("SQL",), (
        ("Snowflake docs", "https://docs.snowflake.com/"),)),

    # --- cloud / devops -------------------------------------------------
    "Docker": _entry("medium", 25, ("Linux",), (
        ("Docker get started", "https://docs.docker.com/get-started/"),)),
    "Kubernetes": _entry("hard", 50, ("Docker",), (
        ("Kubernetes tutorials", "https://kubernetes.io/docs/tutorials/"),)),
    "AWS": _entry("hard", 50, ("Linux",), (
        ("AWS getting started", "https://aws.amazon.com/getting-started/"),)),
    "Azure": _entry("hard", 45, (), (
        ("Microsoft Learn — Azure",
         "https://learn.microsoft.com/en-us/training/azure/"),)),
    "Google Cloud": _entry("hard", 45, (), (
        ("Google Cloud docs", "https://cloud.google.com/docs"),)),
    "CI/CD": _entry("medium", 20, ("Git",), (
        ("GitHub Actions docs",
         "https://docs.github.com/en/actions"),)),
    "GitHub Actions": _entry("easy", 15, ("CI/CD",), (
        ("GitHub Actions docs", "https://docs.github.com/en/actions"),)),
    "Jenkins": _entry("medium", 25, ("CI/CD",), (
        ("Jenkins docs", "https://www.jenkins.io/doc/"),)),
    "Terraform": _entry("hard", 35, ("AWS",), (
        ("Terraform tutorials",
         "https://developer.hashicorp.com/terraform/tutorials"),)),
    "Ansible": _entry("medium", 25, ("Linux",), (
        ("Ansible getting started",
         "https://docs.ansible.com/ansible/latest/getting_started/index.html"),)),
    "Nginx": _entry("medium", 15, ("Linux",), (
        ("Nginx docs", "https://nginx.org/en/docs/"),)),
    "Kafka": _entry("hard", 35, (), (
        ("Kafka documentation", "https://kafka.apache.org/documentation/"),)),

    # --- data / AI ------------------------------------------------------
    "Pandas": _entry("easy", 20, ("Python",), (
        ("Pandas getting started",
         "https://pandas.pydata.org/docs/getting_started/index.html"),)),
    "NumPy": _entry("easy", 15, ("Python",), (
        ("NumPy beginner guide",
         "https://numpy.org/doc/stable/user/absolute_beginners.html"),)),
    "Machine Learning": _entry("hard", 80, ("Python", "Pandas"), (
        ("scikit-learn user guide",
         "https://scikit-learn.org/stable/user_guide.html"),)),
    "TensorFlow": _entry("hard", 50, ("Machine Learning",), (
        ("TensorFlow tutorials", "https://www.tensorflow.org/tutorials"),)),
    "PyTorch": _entry("hard", 50, ("Machine Learning",), (
        ("PyTorch tutorials", "https://pytorch.org/tutorials/"),)),
    "ETL": _entry("medium", 25, ("SQL", "Python"), (
        ("Airflow concepts",
         "https://airflow.apache.org/docs/apache-airflow/stable/core-concepts/index.html"),)),
    "Airflow": _entry("medium", 30, ("Python", "ETL"), (
        ("Airflow docs", "https://airflow.apache.org/docs/"),)),
    "Apache Spark": _entry("hard", 45, ("Python", "SQL"), (
        ("Spark docs", "https://spark.apache.org/docs/latest/"),)),
    "Data Analysis": _entry("medium", 30, ("Pandas",), (
        ("Pandas user guide",
         "https://pandas.pydata.org/docs/user_guide/index.html"),)),
    "Power BI": _entry("easy", 25, (), (
        ("Power BI learning",
         "https://learn.microsoft.com/en-us/power-bi/"),)),
    "Tableau": _entry("easy", 25, (), (
        ("Tableau training", "https://www.tableau.com/learn/training"),)),
    "NLP": _entry("hard", 45, ("Machine Learning",), (
        ("spaCy course", "https://course.spacy.io/"),)),
    "Prompt Engineering": _entry("easy", 10, (), (
        ("Prompt Engineering Guide", "https://www.promptingguide.ai/"),)),

    # --- testing / tools ------------------------------------------------
    "Playwright": _entry("easy", 15, (), (
        ("Playwright docs", "https://playwright.dev/python/docs/intro"),)),
    "Selenium": _entry("medium", 20, (), (
        ("Selenium docs", "https://www.selenium.dev/documentation/"),)),
    "Pytest": _entry("easy", 12, ("Python",), (
        ("Pytest docs", "https://docs.pytest.org/en/stable/"),)),
    "Jest": _entry("easy", 12, ("JavaScript ES6",), (
        ("Jest docs", "https://jestjs.io/docs/getting-started"),)),
    "Cypress": _entry("easy", 15, ("JavaScript ES6",), (
        ("Cypress docs", "https://docs.cypress.io/"),)),
    "SEO": _entry("easy", 15, (), (
        ("Google SEO starter guide",
         "https://developers.google.com/search/docs/fundamentals/seo-starter-guide"),)),
    "Salesforce": _entry("medium", 35, (), (
        ("Salesforce Trailhead", "https://trailhead.salesforce.com/"),)),
    "Figma": _entry("easy", 12, (), (
        ("Figma help centre", "https://help.figma.com/"),)),
}

# Ordering weight for difficulty, so an easy win comes before a hard slog when
# demand is otherwise equal.
DIFFICULTY_RANK = {"easy": 0, "medium": 1, "hard": 2}


def entry_for(skill: str) -> LearningEntry | None:
    """The curated entry for a skill, or None when it isn't mapped yet."""
    return LEARNING_MAP.get(skill)
