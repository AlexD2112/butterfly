# Factions — Petrograd 1917 Setting Pack

## Bolsheviks (player's party)
Growing rapidly in the Soviets through the autumn of 1917, but still a minority nationally and
internally divided on tactics (see Lenin vs. Kamenev/Zinoviev on insurrection timing in
`canon_timeline.md`). The player's `party_standing` resource represents standing specifically
within this faction.

## Mensheviks
The other major Marxist party, split into internationalist and "defensist"/moderate wings,
generally opposed to immediate insurrection and favoring continued cooperation with the
Provisional Government or a broad socialist coalition instead.

## Socialist-Revolutionaries (SRs)
The largest party by raw membership, agrarian-socialist in orientation, also split between a
left wing (increasingly sympathetic to the Bolsheviks) and a right wing (aligned with the
Provisional Government). The Left SRs are a plausible coalition partner for an insurrection.

## Provisional Government
Headed by Kerensky, formally the government of Russia since February 1917, increasingly unable to
resolve the core crises (the war, land, food) that are driving support toward the Soviets. Its
strength is the `government_stability` world track, which the application lowers every day on its
own (faster while bread is short) - watch it erode rather than assuming a fixed collapse date. Its
armed backing is in the forces tracker: the officer cadets, the Women's Battalion, and whatever the
Cossacks can be talked into.

## Petrograd Soviet
Not a party but an institution - the workers' and soldiers' council of the capital. When the
campaign opens it has just passed its first Bolshevik resolution, but the Menshevik-SR presidium
still runs it; winning the chair is the player's first arc. Its plenum is the seat chart that
`call_vote` resolves, and `soviet_authority` measures the player's standing in it.

## The Military Right (Kornilov's circle)
Officers and conservative political figures who attempted a coup against Kerensky in
late August 1917 (days before the campaign start) and failed - discredited but not eliminated.
Tracked as the `counterrevolution_threat` world track, which grows while the government looks too
weak to hold the line; at high values it produces plots, and danger to the player personally.

## The Central Powers / the war
Germany and Austria-Hungary remain Russia's active wartime enemy for the whole opening arc of this
campaign - the war itself (exhaustion, the front, eventually Brest-Litovsk) is a persistent
background pressure, tracked via the `war_exhaustion` world track, not a faction the
player interacts with directly until the diplomatic and military beats in
`canon_timeline.md` (Brest-Litovsk, the founding of the Red Army) become relevant.
