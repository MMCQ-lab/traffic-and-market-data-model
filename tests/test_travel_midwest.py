from datetime import datetime, timezone
from sqlalchemy import create_engine, select
from sqlalchemy.orm import sessionmaker
from src.alt_data.database.base import Base
from src.alt_data.ingestion.traffic.cameras import TravelMidwestCameraIngestor
from src.alt_data.ingestion.traffic.travel_midwest import parse_camera_feed
from src.alt_data.models import DataSource, TrafficCamera


FIXTURE = b'''<cameras><camera id="I-90-001"><CameraLocation>Jane Byrne Interchange</CameraLocation><CameraDirection>WB</CameraDirection><y>41.8786</y><x>-87.6359</x><SnapShot>https://example.test/cam.jpg</SnapShot></camera></cameras>'''
CSV_FIXTURE = b'''CameraID,CameraLocation,CameraDirection,y,x,SnapShot,WarningAge,TooOld,AgeInMinutes,VideoUrl\nI-90-002,Kennedy Expressway,EB,41.8951,-87.6553,https://example.test/cam2.jpg,false,false,3,https://example.test/cam.m3u8\n'''
DOCUMENTED_CSV_FIXTURE = b'''ImgPath,CameraLocation,CameraDirection,y,x,SnapShot,WarningAge,TooOld,AgeInMinutes,VideoUrl\n,Jane Byrne Interchange,NONE,41.8786,-87.6359,https://example.test/cam.jpg,false,false,1,\n'''


def test_parse_camera_feed_preserves_metadata() -> None:
    rows = parse_camera_feed(FIXTURE)
    assert rows[0]["external_camera_id"] == "I-90-001"
    assert rows[0]["direction"] == "WB"
    assert rows[0]["latitude"] == "41.8786"


def test_parse_camera_csv_preserves_metadata() -> None:
    ingestor = TravelMidwestCameraIngestor(None)
    rows = ingestor.validate(CSV_FIXTURE)
    assert rows[0]["external_camera_id"] == "I-90-002"
    assert rows[0]["longitude"] == "-87.6553"
    assert rows[0]["video_url"].endswith(".m3u8")
    assert rows[0]["age_minutes"] == "3"


def test_parse_documented_csv_without_camera_id_ignores_imgpath() -> None:
    rows = TravelMidwestCameraIngestor(None).validate(DOCUMENTED_CSV_FIXTURE)
    assert rows[0]["external_camera_id"].startswith("derived-")
    assert rows[0]["image_url"].endswith("cam.jpg")


def test_camera_save_is_idempotent() -> None:
    engine = create_engine("sqlite:///:memory:")
    Base.metadata.create_all(engine)
    session = sessionmaker(bind=engine)()
    source = DataSource(name="travel_midwest_idot_gateway", base_url="https://travelmidwest.com")
    session.add(source)
    session.flush()
    ingestor = TravelMidwestCameraIngestor(session)
    rows = ingestor.validate(FIXTURE)
    now = datetime.now(timezone.utc)
    assert ingestor.save(source, rows, now) == (1, 0)
    session.flush()
    assert ingestor.save(source, rows, now) == (0, 1)
    assert len(session.scalars(select(TrafficCamera)).all()) == 1
