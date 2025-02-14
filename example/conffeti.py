import random
import time

confetti = [
    ['   ',
     ' · ',
     '   '],
    ['   ',
     ' * ',
     '   '],
    ['· ·',
     ' o ',
     '· ·'],
    [' · ',
     '· ·',
     ' · '],
    ['   ',
     '   ',
     '   '],
]

def sparkle(pilot, color='brightyellow'):

    nbcols, nbrows = pilot.size

    x_cols = random.randint(2, nbcols - 2)
    y_rows = random.randint(2, nbrows - 2)

    time.sleep(0.1 * random.randint(0, 10))
    pilot.draw2d_anim(y_rows, x_cols, confetti, fg=color, overlay=False)

def main(pilot):
    pilot.wait_for_driver()

    for color in ['brightgreen', 'brightmagenta', 'brightblue', 'brightyellow']:
        pilot.drop_task(sparkle, color=color)

    pilot.drop_shell(
        exitmsg='no more confetti :(',
        extra_locals=globals(),
        confirm_exit=False
    )
