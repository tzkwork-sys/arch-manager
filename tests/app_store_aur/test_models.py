from src.app_store.aur.models import AurPackage


def test_aur_package_round_trip_keeps_tuple_fields():
    package = AurPackage(
        name="demo-git",
        package_base="demo-git",
        version="1.2.3.r5-1",
        depends=("python", "qt6-base"),
        licenses=("MIT",),
        keywords=("demo",),
        maintainer=None,
    )

    restored = AurPackage.from_dict(package.to_dict())

    assert restored == package
    assert restored.orphaned is True
