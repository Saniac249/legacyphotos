from PIL import Image, ImageChops, ImageStat
import sys
import math

if len(sys.argv) != 4:
    print("Usage: python compare_render.py ours.jpg apple.jpg diff.jpg")
    sys.exit(1)

ours_path = sys.argv[1]
apple_path = sys.argv[2]
diff_path = sys.argv[3]

ours = Image.open(ours_path).convert("RGB")
apple = Image.open(apple_path).convert("RGB")

print("Ours:", ours.size)
print("Apple:", apple.size)

ours = ours.resize(
    apple.size,
    Image.Resampling.LANCZOS
)

diff = ImageChops.difference(ours, apple)
diff.save(diff_path)

stat = ImageStat.Stat(diff)

rmse = math.sqrt(
    sum(v ** 2 for v in stat.rms) / len(stat.rms)
)

mean_error = sum(stat.mean) / len(stat.mean)

print()
print("Mean absolute difference:", mean_error)
print("RMSE:", rmse)
print("Saved diff:", diff_path)