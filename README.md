# Stratégie "Envelop"

Stratégie de type "Market Macking"
Créer une enveloppe d'ordres limits basée sur un décalage de la moyenne mobile.
Les principaux paramètre de la stratégie sont :
- La liste des coins
- La taille de fenetre de moyenne mobile
- les % de déclage de la moyenne mobile pour créer les enveloppes
- un trigger de revante exprimé en % au dessus de MM
- Money Management : allocation de pondération du portefeuille par coin 

## Set up

> git clone https://github.com/vdieudonne/Envelop.git

> bash Envelop/install.sh
